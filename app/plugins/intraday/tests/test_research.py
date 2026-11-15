# -*- coding: utf-8 -*-
"""板块与个股研究卡（frontend/research.js）守门测试。

这一块是照旧页 engine/web/index.html:4306-4531 逐函数搬过来的：搬错不会报错，
只会显示错的数 / 点不动。所以测三件事：
  ① 渲染与旧页真身逐字节对齐（node:vm 直接跑旧页源码，不是跑我抄的字符串）；
  ② 交互全集（层级 / 排序 / 选板块 / 返回全A / 回成分股）真在 DOM 上跑通；
  ③ loadResearch 的三条自愈（8s 超时 / 15s 悬挂释放 / 实时→收盘回落）没在搬运中丢掉。

做法与 test_caliber.py 一致：Node 加载真实模块。见 research_align.mjs / research_dom.mjs。
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
RESEARCH = FE / "research.js"
RESEARCH_CSS = FE / "research.css.js"
FRONTEND = PLUGIN / "frontend.js"
# PANEL=projects/panel-v2 → parents[0]=projects → parents[1]=OH-WorkSpace（仓库根）
LEGACY = PANEL.parents[1] / "engine" / "web" / "index.html"
HAS_LEGACY = LEGACY.is_file()
JSDOM = PANEL.parents[1] / "node_modules" / "jsdom" / "lib" / "api.js"
HAS_JSDOM = JSDOM.is_file()


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
    head = "const A = await import('file:///%s');\n" % RESEARCH.as_posix()
    proc = subprocess.run(["node", "--input-type=module", "-e", head + body],
                          capture_output=True, text=True, timeout=timeout, cwd=str(PANEL))
    if proc.returncode != 0:
        pytest.fail("node 执行失败：\n%s\n%s" % (proc.stdout[-3000:], proc.stderr[-3000:]))
    lines = [x for x in proc.stdout.strip().splitlines() if x.strip()]
    return json.loads(lines[-1])

# ---------------------------------------------------------------- 静态约束（四条约束 + 门禁）

def test_research_has_no_dom_or_network_shortcuts() -> None:
    """约束②③：不用 getElementById、不挂 body、不 position:fixed；取数只走注入的 api.get。"""
    src = RESEARCH.read_text(encoding="utf-8")
    for banned in ("fetch(", "localStorage", "document.body", "position:fixed", "position: fixed"):
        assert banned not in src, "research.js 出现 %s" % banned
    for line in src.splitlines():
        if "getElementById" in line or "onclick=" in line or "onscroll=" in line:
            assert line.lstrip().startswith("*"), "非注释行仍在用旧页写法：%s" % line


def test_research_has_no_module_level_state() -> None:
    """约束①：状态必须每实例（createState 返回的 st），不许模块级变量攒状态。"""
    src = RESEARCH.read_text(encoding="utf-8")
    bad = re.findall(r"^let\s+_?[A-Za-z_$]", src, re.M)
    assert not bad, "research.js 出现模块级 let（会跨实例串味）：%s" % bad
    assert "new AbortController()" in src, "每实例必须自带 AbortController"
    assert "clearInterval(st.watch)" in src, "看门狗必须随实例卸载清掉"


def test_research_writes_no_raw_hex_or_rgba() -> None:
    for name in ("research.js", "research.css.js"):
        src = (FE / name).read_text(encoding="utf-8")
        assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", src), "%s 出现裸 hex" % name
        assert not re.search(r"rgba?\(", src), "%s 出现自拼 rgba" % name


def test_research_css_scoped_and_tokens_exist() -> None:
    """颜色只走 token（门禁 test_no_raw_hex_in_any_card 同款要求）+ 选择器全部加 .it-card。"""
    head = "const A = await import('file:///%s');\n" % RESEARCH_CSS.as_posix()
    proc = subprocess.run(["node", "--input-type=module", "-e", head +
                           "console.log(JSON.stringify({ css: A.RESEARCH_CSS }));"],
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
            assert ".it-card .rs-" in s, "单行 @media 内层没加作用域：%s" % s
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


def test_frontend_js_wires_research_section() -> None:
    """编排接入只许一处：frontend.js 里建实例 / 绑事件 / 卸载 / 刷新各一处。"""
    src = FRONTEND.read_text(encoding="utf-8")
    assert "import * as research from './frontend/research.js';" in src
    assert "injectResearchCss(document)" in src
    assert src.count("research.createState(") == 1
    assert src.count("research.bind(") == 1
    assert src.count("research.unbind(") == 1
    assert "S.research.load(" in src
    assert "板块与个股研究" in src, "卡片区块标题"
    # 研究卡自带 TTL（旧页 4337），不许被卡片 20s 的 render() 顺手重绘（重绘会抹掉滚动位置与选中态）
    assert "setHtml(S.el.secResearch" not in src, "研究卡不得进 render() 的 setHtml 列表"


def test_research_constants_match_legacy() -> None:
    """旧页 4313/4337/4349/4330/4374 的常量逐个对上（踩坑攒的数字，不许改）。"""
    out = _expr("console.log(JSON.stringify({ srt: A.RS_SRT, levels: A.RS_LEVELS,"
                " ttls: [A.RS_TTL_LIVE, A.RS_TTL_CLOSE], req: A.REQ_TIMEOUT_MS,"
                " busy: A.BUSY_RELEASE_MS, dog: A.WATCHDOG_MS }));")
    assert out["srt"] == {"contrib": "指数贡献", "px_pct": "涨跌", "amv_pct": "AMV"}
    assert out["levels"] == [["L2", "同花顺二级"], ["CONCEPT", "自建"], ["L3", "同花顺三级"]]
    assert out["ttls"] == [30000, 120000], "盘中 30s / 盘外 120s"
    assert out["req"] == 8000 and out["busy"] == 15000 and out["dog"] == 20000


# ---------------------------------------------------------------- 纯函数口径

def test_rs_fmt_and_sort() -> None:
    """rsFmt 缺值破折号；rsSort 缺值排最后（不能当 0 夹在中间）。"""
    out = _expr("""
const rows = [
  { c: 'A', contrib: 2.391, px_pct: null, amv_pct: 7.25 },
  { c: 'B', contrib: -1.5,  px_pct: 3.0,  amv_pct: null },
  { c: 'C', contrib: null,  px_pct: 1.0,  amv_pct: 26.77 },
];
console.log(JSON.stringify({
  fmt: [A.rsFmt(2.391,'‰',3), A.rsFmt(-1.5,'%'), A.rsFmt(null,'%'), A.rsFmt(0,'%'), A.rsFmt(NaN,'%')],
  byContrib: A.rsSort(rows,'contrib').map(x=>x.c),
  byPx: A.rsSort(rows,'px_pct').map(x=>x.c),
  byAmv: A.rsSort(rows,'amv_pct').map(x=>x.c),
  holesLast: A.rsSort(rows,'px_pct').map(x=>x.px_pct),
}));
""")
    assert out["fmt"] == ["+2.391‰", "-1.50%", "—", "0.00%", "—"]
    assert out["byContrib"] == ["A", "B", "C"], "缺贡献排最后"
    assert out["byPx"] == ["B", "C", "A"]
    assert out["byAmv"] == ["C", "A", "B"]
    assert out["holesLast"] == [3.0, 1.0, None]


def test_rs_sp_and_kname() -> None:
    """缩略线三条 path；kName 可跳带占位、不可跳显式 .k-nolink（不伪装可点）。"""
    out = _expr("""
const sp = A.rsSp({ up:'M0 1L1 2', dn:'M0 5L1 4', ma:'M0 3L1 3' });
console.log(JSON.stringify({
  spN: (sp.match(/<path/g)||[]).length,
  cls: (sp.match(/class="sp-[a-z]+"/g)||[]),
  bare: A.rsSp(null), zero: A.rsSp({}),
  playable: ['600519.SH','000001.SZ','830799.BJ','600519','000001','100001','830799','']
    .map(x=>A.kTsPlayable(x)),
  kGood: A.kName('贵州茅台','600519.SH'),
  kBad: A.kName('某票','100001'),
  kEmpty: A.kName('',''),
}));
""")
    assert out["spN"] == 3 and out["cls"] == ['class="sp-dn"', 'class="sp-up"', 'class="sp-ma"']
    assert out["bare"] == '<span class="muted">—</span>'
    assert out["zero"] == '<span class="muted">—</span>', "零值也当无数据（旧页 !sp.up && !sp.dn）"
    assert out["playable"] == [True, True, True, True, True, False, False, False]
    assert 'data-open-kline="600519.SH"' in out["kGood"], "可跳码必须带跨 iframe 桥占位"
    assert 'class="k-nolink"' in out["kBad"]
    assert out["kEmpty"] == "—"


def test_asof_label_four_states() -> None:
    """旧页 4486-4495 口径四态；15:00 整是「实时 / 收盘定格」分界。"""
    out = _expr("""
const mk = (l,d,a,n) => A.asofText({ live_mode:l, live_date:d, asof:a }, new Date(n));
console.log(JSON.stringify({
  liveNow:  mk(true,  '2026-10-26', '10:31:02', '2026-10-26T10:31:00'),
  liveCls:  mk(true,  '2026-10-26', '15:00:56', '2026-10-26T15:10:00'),
  liveOld:  mk(true,  '2026-10-23', '15:00:56', '2026-10-26T10:00:00'),
  close:    mk(false, null,         '2026-10-23', '2026-10-26T10:00:00'),
  edge1459: mk(true,  '2026-10-26', '14:59:59', '2026-10-26T14:59:00'),
  edge1500: mk(true,  '2026-10-26', '15:00:01', '2026-10-26T15:00:00'),
}));
""")
    assert out["liveNow"] == "实时 10:31:02"
    assert out["liveCls"] == "收盘定格 15:00:56"
    assert out["liveOld"] == "末帧 2026-10-23 15:00:56（非今日）", "绝不冒充今天的实时"
    assert out["close"] == "收盘 2026-10-23"
    assert out["edge1459"] == "实时 14:59:59"
    assert out["edge1500"] == "收盘定格 15:00:01"


def test_parts_rows_follow_susp_threshold() -> None:
    """贡献分项：停牌项 |susp| < 0.01 时不出现（没有就不画一排 0），出现时是 6 条。"""
    out = _expr("""
const mk = (susp) => A.rsPartsHtml({ rs: { sel:'881121.TI', sel_name:'半导体', sel_parts: {
  px: 1.2, sh: -0.4, act: 0.8, new: 0.1, susp: susp, den: -0.2, total: 2.391 } } });
const h5 = mk(0.0), h6 = mk(-0.05);
console.log(JSON.stringify({
  n5: (h5.match(/class="rs-part[ "]>/g)||[]).length,
  n6: (h6.match(/class="rs-part[ "]>/g)||[]).length,
  suspIn6: /停牌/.test(h6), suspIn5: /停牌/.test(h5),
  total: (h5.match(/合计 <b class="up">\\+2\\.391/)||[]).length,
  btn: /data-open-amv="881121\\.TI"/.test(h5),
}));
""")
    assert out["n5"] == 5 and out["n6"] == 6
    assert out["suspIn6"] and not out["suspIn5"]
    assert out["total"] == 1 and out["btn"]


def test_all_scope_parts_block_uses_all_parts() -> None:
    """全A 视图的贡献分项（2026-11-06 主人）：读 all_parts、标题沪深全A、不挂工作台详情。

    背景：原先 allMode 整块不画，全A 视图看不到分解；后端 scope=all 现在补 `all_parts`。
    这里锁四件事：① 数据源必须是 all_parts（不是 sel_parts）；② 标题为「沪深全A / ALL」；
    ③ 全A 不是板块，不得挂 data-open-amv 按钮；④ 合计按六项实算且脚注说清口径。
    """
    out = _expr("""
const st = { rs: { scope: 'all', sel: '881121.TI', sel_name: '半导体', sel_parts: {
  px: 99, sh: 99, act: 99, new: 99, susp: 99, den: 99, total: 99 },
  all_parts: { px: 12.5, sh: 0.1, act: 30.2, new: 0.0, susp: 0.0, den: -42.8, total: 0 } } };
const h = A.rsPartsHtml(st, true);
console.log(JSON.stringify({
  name: /沪深全A/.test(h), code: /ALL · 贡献分项/.test(h),
  n: (h.match(/class="rs-part(?: zero)?">/g)||[]).length,
  btn: /data-open-amv/.test(h),
  total: /合计 <b class="">0[.]000‰/.test(h),
  note: /明细与全A 总量对齐/.test(h),
  selLeak: /99[.]000‰/.test(h),
}));
""")
    assert out["name"] and out["code"]
    assert out["n"] == 5, "停牌 |susp|<0.01 不画（四档 + 大盘基准）"
    assert out["btn"] is False, "全A 不是板块，不能挂工作台详情按钮"
    assert out["total"] and out["note"]
    assert out["selLeak"] is False, "全A 必须读 all_parts，不能碍到板块的 sel_parts"


# ---------------------------------------------------------------- 取数自愈（旧页 4327 三条）

def test_loader_timeout_busy_ttl_and_fallback() -> None:
    """8s 超时 / 15s 悬挂释放 / 实时→收盘回落 / TTL 缓存，四条都要真成立。"""
    out = _expr("""
const st0 = () => ({ api: null, el: null, body: null, alive: true, rs: null, lvl: 'L2', sel: '',
  srt: 'contrib', busy: false, busyAt: 0, err: '', at: 0, live: 1, scope: 'board',
  scroll: {}, node: null, sig: '', watch: 0 });
const res = {};

// ① 正常：带 live=1、带 signal
{
  const calls = [];
  const st = st0();
  st.api = { async get(p, init) { calls.push({ p, signal: !!(init && init.signal) });
    return { ok: true, sel: 'SEL1', boards: [], members: [] }; } };
  await A.load(st, true);
  res.normal = { url: calls[0].p, signal: calls[0].signal, ok: !!st.rs, sel: st.sel, busy: st.busy };
}
// ② 实时失败 → 自动回落收盘口径一次（第二个 URL 不带 live=1）
{
  const urls = [];
  const st = st0();
  st.api = { async get(p) { urls.push(p);
    if (p.indexOf('live=1') >= 0) throw new Error('boom');
    return { ok: true, sel: 'SEL2', boards: [], members: [] }; } };
  await A.load(st, true);
  res.fallback = { n: urls.length, urls, ok: !!st.rs };
}
// ③ 8s 超时：截获 setTimeout 立刻触发 → abort → err=请求超时（8s）
{
  const st = st0();
  st.api = { async get(p, init) { return new Promise((ok, no) => {
    init.signal.addEventListener('abort', () => no(Object.assign(new Error('x'), { name: 'AbortError' })));
  }); } };
  const realST = globalThis.setTimeout, realCT = globalThis.clearTimeout;
  let delay = null;
  globalThis.setTimeout = (fn, ms) => { delay = ms; return realST(fn, 0); };
  globalThis.clearTimeout = (id) => realCT(id);
  await A.load(st, true, true, 0);
  globalThis.setTimeout = realST; globalThis.clearTimeout = realCT;
  res.timeout = { delay, err: st.err, rs: st.rs, busy: st.busy };
}
// ④ 悬挂 15s 才释放；不足 15s 不重入
{
  const calls = [];
  const st = st0();
  st.api = { async get(p) { calls.push(p); return { ok: true, boards: [], members: [] }; } };
  st.busy = true; st.busyAt = Date.now() - 1000;
  await A.load(st, true); res.busyFresh = calls.length;
  st.busy = true; st.busyAt = Date.now() - 16000;
  await A.load(st, true); res.busyStale = calls.length;
}
// ⑤ TTL：盘中 30s 内命中缓存不重拉；超过就重拉；live=0 走 120s
{
  const calls = [];
  const st = st0();
  st.api = { async get(p) { calls.push(p); return { ok: true, boards: [], members: [] }; } };
  st.rs = { ok: true, boards: [], members: [] };
  st.at = Date.now() - 1000;   await A.load(st, false); res.ttlHit = calls.length;
  st.at = Date.now() - 31000;  await A.load(st, false); res.ttlMiss = calls.length;
  st.live = 0; st.at = Date.now() - 60000; await A.load(st, false); res.ttlClose = calls.length;
}
// ⑥ 看门狗：createState 建定时器，unbind 清掉
{
  const st = A.createState({}, {}, { async get() { return {}; } });
  res.dog = { created: st.watch !== 0, ms: A.WATCHDOG_MS };
  A.unbind(st);
  res.dog.after = st.watch; res.dog.alive = st.alive;
}
console.log(JSON.stringify(res));
""", timeout=180)
    assert out["normal"]["url"] == "/api/amv_research.json?level=L2&live=1"
    assert out["normal"]["signal"] is True, "请求必须带 AbortSignal"
    assert out["normal"]["ok"] and out["normal"]["sel"] == "SEL1" and out["normal"]["busy"] is False
    assert out["fallback"]["n"] == 2, "实时失败必须回落一次"
    assert "live=1" in out["fallback"]["urls"][0] and "live=1" not in out["fallback"]["urls"][1]
    assert out["fallback"]["ok"]
    assert out["timeout"]["delay"] == 8000, "8s 超时定时器"
    assert out["timeout"]["err"] == "请求超时（8s）"
    assert out["timeout"]["busy"] is False, "超时后必须释放 busy（旧页踩过永久加载中）"
    assert out["busyFresh"] == 0 and out["busyStale"] == 1, "15s 内不重入、超 15s 强制释放"
    assert out["ttlHit"] == 0 and out["ttlMiss"] == 1
    assert out["ttlClose"] == 1, "live=0 走 120s 档（60s 已过，应重拉）"
    assert out["dog"]["created"] is True and out["dog"]["ms"] == 20000
    assert out["dog"]["after"] == 0 and out["dog"]["alive"] is False


# ---------------------------------------------------------------- 与旧页逐字节对齐

@pytest.mark.skipif(not HAS_LEGACY, reason="缺旧页 engine/web/index.html（只读参考）")
def test_render_is_byte_identical_to_legacy() -> None:
    """html() 与旧页 _rsHtml() 逐字节相同（规范化掉四条约束造成的差异后）。

    5 组参数（3 排序键 × 板块/全A）全比；另比行数 90/183、6 列、空态、失败态。
    """
    out = _node(TESTS / "research_align.mjs")
    for r in out["rows"]:
        assert r["equal"], "与旧页不一致：%s（at=%d）\nlegacy: %s\nnew   : %s" % (
            r["label"], r["at"], r["ctxA"], r["ctxB"])
        assert r["boards"] == 90, "左栏板块行 %s" % r["label"]
        assert r["members"] in (183, 300), "中栏行数"
        assert r["cols"] == 6, "6 列"
    assert out["counts"]["boards"] == 90 and out["counts"]["members"] == 183
    assert out["emptyLegacy"] == out["emptyNew"], "空态文案必须与旧页一字不差"
    assert "板块与个股研究数据不可用：接口不可达" in out["errNew"], "失败态如实报错"


# ---------------------------------------------------------------- DOM 交互（jsdom）

@pytest.mark.skipif(not HAS_JSDOM, reason="缺 jsdom（OH-WorkSpace/node_modules/jsdom）")
def test_dom_interactions() -> None:
    """交互全集在真 DOM 上跑：初始渲染 / 排序 3 键 / 点行选板块 / 返回全A / 回成分股 / 清理。"""
    out = _node(TESTS / "research_dom.mjs")
    assert out["initial"]["left"] == 90 and out["initial"]["mid"] == 183
    assert out["initial"]["cols"] == 6 and out["initial"]["spark"] == 273
    assert out["initial"]["kname"] == 183, "成分股名全部可点"
    s = out["sort"]
    assert len({s["contrib"], s["px_pct"], s["amv_pct"]}) == 3, "三键排序结果必须互不相同"
    assert s["apiCalls"] == 1, "排序不许重拉数据（旧页同上）"
    assert s["headerOn"] == ["px_pct", "px_pct", "amv_pct", "amv_pct"]
    assert out["select"]["sel"] == out["select"]["expect"]
    assert "code=" in out["select"]["url"]
    assert out["all"]["scope"] == "all" and "scope=all" in out["all"]["url"]
    assert out["all"]["midRows"] == 300 and out["all"]["back"] == "← 回成分股"
    assert out["backToBoard"]["scope"] == "board"
    assert out["level"]["lvl"] == "L3" and "level=L3" in out["level"]["url"]
    assert out["ctx"]["sector"] == 90 and out["ctx"]["stock"] == 183
    assert out["ctx"]["openAmv"] == 1 and out["ctx"]["openKline"] == 183
    assert out["scroll"]["captured"] == 123 and out["scroll"]["restored"] == 123, "滚动位置保持"
    assert out["cleanup"]["alive"] is False and out["cleanup"]["watch"] == 0
    assert out["cleanup"]["ignored"] is True, "unbind 后点击必须不再响应"


def test_back_to_all_survives_click_during_inflight_request() -> None:
    """「返回全A」在请求在飞时被点，不得静默丢失（2026-10-26 主人报「点没反应」）。

    旧版本的缺陷（两步叠加）：
      ① `rsSet` 先改 st.scope 再调 load，而 load 碰到 busy 直接 return —— 这次点击没了；
      ② 在飞响应回来后按**新** scope 渲染**旧**数据 → 标题/按钮与表格互相矛盾；
      ③ 签名只含 st.scope 不含数据自报的 scope / members 行数 → 补发的全A 数据到了也不重绘。

    本机直连时窗口只有 ~100ms（C2a 验收就是这样过的），盘中数据大/网络一慢就必现。
    jsdom 里用一道「闸门」把响应留住，稳定造出现场，测三件事：
      ① 点击当时确实在飞（busy=True），且 DOM 还没变（用户看到的就是「没反应」）；
      ② 点击没丢：st.pending 置位；
      ③ 放行后自动补发 scope=all 请求，最终：scope=all、按钮变「回成分股」、中栏行数 = 全A 行数。
    另跑三态循环：全A → 回成分股 → 又回全A。
    """
    out = _node(TESTS / "research_dom.mjs")
    r = out["raceClick"]
    assert r["busyAtClick"] is True, "这一例必须真发生在请求在飞时（否则测不到该缺陷）"
    assert r["rightAfterClick"]["scope"] == "all", "点击要立刻记下用户意图"
    assert r["rightAfterClick"]["pending"] is True, "被 busy 闸门拦下的强制请求必须排队，不得丢弃"
    assert r["rightAfterClick"]["backLabel"] in ("返回全A", "返回全A…"), "按钮文案应还是「返回全A」系"
    # 2026-10-26 加了忙碌态后，点击当时按钮会变成「返回全A…」（表格仍未重绘）。
    # 旧版本这里完全无反应 —— 现在有反馈了，但数据仍是旧的，两者靠“表格没变”区分。
    assert r["rightAfterClick"]["backLabel"] == "返回全A…", \
        "忙碌态没出来（主人要求的反馈就是这一下）：%s" % r["rightAfterClick"]["backLabel"]
    assert r["issuedScopeAll"] is True, "放行后必须补发 scope=all 请求"
    assert r["pendingCleared"] is True, "补发后 pending 必须清掉（否则会一直重试）"
    assert r["finalScope"] == "all" and r["finalBack"] == "← 回成分股"
    assert r["finalMidRows"] == r["expectMidRows"] == 300, (
        "最终中栏必须是全A 的 %s 行，实测 %s 行（数据没到 / 到了没重绘）"
        % (r["expectMidRows"], r["finalMidRows"]))
    t = out["threeState"]
    assert t["ok"] is True, "三态循环必须都通：%s" % json.dumps(t, ensure_ascii=False)
    assert t["toBoard"]["scope"] == "board" and t["toBoard"]["back"] == "返回全A"
    assert t["backToAll"]["scope"] == "all" and t["backToAll"]["back"] == "← 回成分股"


# ---------------------------------------------------------------- 盘中实时自愈（2026-10-27）

def test_watchdog_retries_stale_data_in_session() -> None:
    """标注 N20261027-101349-33「盘中研究区用昨日数据」：看门狗不得因「有旧数据」永不重试。

    旧实现的缺陷：看门狗第一句是 `if (st.rs) return;`。只要手里有**任何**旧数据
    （包括后端实时取不到后回退的收盘帧），就再也不会自动重试 → 盘中永久停在昨日数据，
    用户只能手动切层级/刷新才能撞上一次重试。
    本测用「截获 setInterval 回调 + 假时钟」直接驱动看门狗，验：
      ① TTL 内不打扰（不白烧 QMT）；
      ② TTL 到期 + 已有旧 rs + 盘中窗口 → 必须重试；
      ③ 盘后 / 周末钟点 TTL 到期也不刷历史帧（保持原 TTL 语义）；
      ④ 上一轮实时失败（err/liveErr）时同样重试，且重试请求仍带 live=1；
      ⑤ 纯函数 watchdogDue 九种情形逐个对（含 live=0 不刷、无数据立刻刷）。
    """
    out = _node(TESTS / "research_dom.mjs")
    w = out["watchdog"]
    assert w["hasOld"] is True, "这一例必须发生在「手里已有旧数据」时（否则测不到该缺陷）"
    assert w["ttlFresh"] == 0, "TTL 内不该重试"
    assert w["ttlExpired"] == 1, "TTL 到期且盘中：有旧 rs 也必须重试（本轮修的核心）"
    assert w["stillHasOld"] is True, "重试期间旧数据要保留（不白屏）"
    assert w["offHours"] == 0, "盘后不自动刷历史帧"
    assert w["weekend"] == 0, "周末不自动刷历史帧"
    assert w["afterFail"] == 1, "上一轮实时失败 → TTL 到期必须再试"
    assert "live=1" in w["lastUrl"], "重试必须是实时口径（否则永远回不到 live）"
    d = w["due"]
    assert d["noData"] is True, "无数据时立即重试（原语义）"
    assert d["fresh"] is False
    assert d["expired"] is True
    assert d["offHours"] is False and d["weekend"] is False
    assert d["closeMode"] is False, "live=0（用户显式要收盘口径）不自动刷"
    assert d["closeNoErr"] is True, "盘中窗口内拿到收盘帧 = 降级结果，必须继续试实时"
    assert d["srvLiveErr"] is True and d["localErr"] is True


def test_live_err_visible_when_fallback_to_close() -> None:
    """标注 N20261027-101349-33：实时不可用回落收盘时，不能只写「收盘」把失败藏掉。

    服务端自报（rs.live_err）与本轮前端失败（st.liveErr）两个来源都要在口径行里明说，
    并且真实时帧不能凭空冒出这个提示。
    """
    out = _node(TESTS / "research_dom.mjs")
    e = out["liveErr"]
    assert e["srvHasText"], "服务端 live_err 必须显示「实时不可用，当前为收盘」"
    assert e["srvHasReason"], "原因（timeout）要可见"
    assert e["srvAsof"], "日期仍要如实给（收盘 2026-10-23）"
    assert e["localHasText"] and e["localHasReason"], "前端本轮实时失败也要显示"
    assert e["liveNoWarn"], "真实时帧不得出现降级提示（不误报）"
    assert e["liveAsof"], "真实时帧仍是「实时 HH:MM:SS」"


def test_back_button_shows_busy_state() -> None:
    """主人 2026-10-26：「返回全A」点下去到数据回来有 1~1.5s 静默期，按钮必须有反馈。

    形态选的是「禁用 + 文案变…+ 小脉冲点」（与卡里「补齐缺口 → 检查中…」同一套语气），
    不弹框、不遮表。四件事：
      ① 请求在飞时：disabled / aria-busy=true / class=busy / 文案带「…」；
      ② 本来可点（idle 时 disabled=false、aria-busy=false）；
      ③ 响应回来全部复原（不把用户锁在忙碌态）；
      ④ 复原后数据是真到了（中栏行数跟着变）。
    """
    out = _node(TESTS / "research_dom.mjs")
    b = out["busyState"]
    assert b["idleText"] == "返回全A" and b["idleAttrs"]["disabled"] is False
    assert b["idleAttrs"]["aria"] == "false" and b["idleAttrs"]["cls"] is False
    d = b["during"]
    assert d["busy"] is True, "这一例必须真发生在请求在飞时"
    assert d["disabled"] is True, "忙碌时必须禁用（再点没意义）"
    assert d["aria"] == "true" and d["cls"] is True, "忙碌态可被无障碍读屏/样式识别"
    assert d["text"].endswith("…"), "忙碌文案要有「…」：%s" % d["text"]
    assert d["disabled"] and d["text"] != b["idleText"], "文案确实变了（不是只加禁用）"
    a = b["after"]
    assert a["busy"] is False and a["disabled"] is False
    assert a["aria"] == "false" and a["cls"] is False, "响应回来必须复原忙碌态"
    assert a["text"].startswith("←"), "全A 之后按钮回到「← 回成分股」：%s" % a["text"]
    assert a["midRows"] == 300, "复原时数据也得真到了（%s 行）" % a["midRows"]
