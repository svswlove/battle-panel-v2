# -*- coding: utf-8 -*-
"""日内演化（frontend/evo.js）守门测试。

这一块是从旧页 engine/web/index.html:3678-4313 搬的（`_evo*` 55 个函数），风险不在「算错」，
而在「搬着搬着走形」——所以测三件事：

  ① **静态约束**（四条血泪约束的落地形态）：
     · 零 `getElementById` / 零 `document.querySelector`（全走容器内查询）
     · 零模块级可变状态（17 个旧页模块级变量必须下沉到每实例 state）
     · 每实例自带 AbortController
     · 零内联 `onclick=`（全部改 data-act 委托）
     · `_evoFitH` 那个空函数必须还在（旧页调用点不删）
  ② **与旧页逐字节对齐**（tests/evo_align.mjs 在 node:vm 里跑旧页真源码）：
     4 组配置下，抹掉「onclick→data-act / 三个 id」后**整段 HTML 完全相同**，
     并且每张图的 path `d` 逐字相同（曲线数据没走形）。
  ③ **调色板例外是「真在用」**：见 app/tests/test_fe_shell_bridge.py 的
     `test_evo_palette_exception_is_scoped_and_used`（登记了就不得是空头白名单）。

做法与 test_research.py / test_audit.py 一致：Node 加载真实模块 + node:vm 跑旧页源码。
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
EVO = FE / "evo.js"
FRONTEND = PLUGIN / "frontend.js"
# PANEL=projects/panel-v2 → parents[0]=projects → parents[1]=OH-WorkSpace（仓库根）
LEGACY = PANEL.parents[1] / "engine" / "web" / "index.html"
HAS_LEGACY = LEGACY.is_file()


def _node(script_path: Path, timeout: int = 300) -> dict:
    """跑 tests/*.mjs 运行器，取最后一行 JSON。"""
    proc = subprocess.run(["node", str(script_path)], capture_output=True, text=True,
                          timeout=timeout, cwd=str(PANEL))
    if proc.returncode != 0:
        pytest.fail("node 执行失败：\n%s\n%s" % (proc.stdout[-3000:], proc.stderr[-3000:]))
    lines = [x for x in proc.stdout.strip().splitlines() if x.strip()]
    return json.loads(lines[-1])


def _expr(body: str, timeout: int = 120) -> dict:
    head = "const E = await import('file:///%s');\n" % EVO.as_posix()
    proc = subprocess.run(["node", "--input-type=module", "-e", head + body],
                          capture_output=True, text=True, timeout=timeout, cwd=str(PANEL))
    if proc.returncode != 0:
        pytest.fail("node 执行失败：\n%s\n%s" % (proc.stdout[-3000:], proc.stderr[-3000:]))
    lines = [x for x in proc.stdout.strip().splitlines() if x.strip()]
    return json.loads(lines[-1])


# ---------------------------------------------------------------- ① 静态约束


def test_evo_exists_and_is_not_a_stub() -> None:
    assert EVO.is_file(), "缺 frontend/evo.js"
    src = EVO.read_text(encoding="utf-8")
    assert len(src) > 20000, "evo.js 像空壳（%d 字节）" % len(src)
    # 三张图 + 四段结构都要有真实现，不能只留函数名
    for token in ("data-evo=\"amv\"", "data-evo=\"quote\"", "data-evo=\"contrib\"",
                  "evo-split", "evo-pane", "evo-cw", "evo-rank", "evo-lg",
                  "evo-card", "evo-bf"):
        assert token in src, "evo.js 缺 %s" % token


def _code_only(path: Path) -> str:
    """只取代码，清掉块注释与行注释。

    为什么要这一层：文件头的「搬法说明」里会提到 `getElementById`、` onclick=` 这些词
    （讲的正是「不能这么写」），不清掉会把自己的注释当成违规命中。
    """
    src = path.read_text(encoding="utf-8")
    src = re.sub(r"/\*[\s\S]*?\*/", "", src)
    src = re.sub(r"(?m)^\s*//.*$", "", src)
    return src


def test_evo_has_no_dom_id_shortcuts() -> None:
    """约束②：零 getElementById / 零 document.querySelector（旧页 5 个 id 全部换容器内查询）。"""
    src = _code_only(EVO)
    for banned in ("getElementById", "document.querySelector", "document.querySelectorAll",
                   "document.body", "position:fixed", "position: fixed"):
        assert banned not in src, "evo.js 出现 %s（应改为容器内查询）" % banned
    # 正例：确实在自己的 body 里查
    assert "st.body.querySelector" in src or "st.body.querySelectorAll" in src


def test_evo_state_is_per_instance() -> None:
    """约束①：17 个旧页模块级变量不得以模块级 let 形式存在；每实例自带 AbortController。"""
    src = EVO.read_text(encoding="utf-8")
    bad = re.findall(r"^let\s+_?[A-Za-z_$]", src, re.M)
    assert not bad, "evo.js 出现模块级 let（会跨实例串味）：%s" % bad
    assert "new AbortController()" in src, "每实例必须自带 AbortController"
    # 关键状态位必须挂在 state 上（createState 返回对象里出现）
    for field in ("dir:", "mode:", "srt:", "sel:", "hover:", "cursor:", "idxOn:",
                  "range:", "rangeDrag:", "drag:", "win:", "bfState:"):
        assert field in src, "state 缺字段 %s" % field


def test_evo_has_no_inline_onclick() -> None:
    """约束④：内联 onclick 一律改 data-act 委托（旧页 6 类内联调用）。"""
    src = _code_only(EVO)
    assert " onclick=" not in src, "evo.js 里还有内联 onclick"
    # data-act 属性由 seg() 拼出（act 是变量），所以查「动作名」与「分发分支」两处
    for act in ("evo-mode", "evo-dir", "evo-srt", "evo-idx", "evo-bf", "evo-clear", "evo-goto"):
        assert "'" + act + "'" in src, "缺动作 %s" % act
        assert "a === '" + act + "'" in src, "bind 里没分派 %s（按钮会点了没反应）" % act


def test_evo_keeps_fith_empty_function() -> None:
    """旧页 4090 `_evoFitH` 是空函数，调用点不删（2026-10-18 修过「量高写回→正反馈越算越大」）。"""
    src = EVO.read_text(encoding="utf-8")
    assert re.search(r"function fitH\(\)\s*\{[^}]*\}", src), "缺 fitH 空函数"
    assert "fitH();" in src, "paint 里必须还调 fitH()"
    body = re.search(r"function fitH\(\)\s*\{([^}]*)\}", src).group(1)
    assert "getBoundingClientRect" not in body, "fitH 不得再量高（会重演正反馈）"


def test_frontend_wires_evo_section() -> None:
    """编排接入：import / 建实例 / 绑 / 解绑各一处；轨迹缓存出口开在 frontend.js 状态上。"""
    src = FRONTEND.read_text(encoding="utf-8")
    # 2026-10-27：相对 import 带 ?v=（浏览器强刷子模块用；值跟 manifest 版本走）
    assert re.search(r"import \* as evo from './frontend/evo\.js(\?v=[0-9.]+)?';", src)
    assert src.count("evo.createState(") == 1
    assert src.count("evo.bind(") == 1
    assert src.count("evo.unbind(") == 1
    assert "evo.paint(S.evo)" in src
    assert "日内演化 · 活跃指数与指数贡献" in src, "卡片区块标题"
    # 缓存出口必须改的是本文件的 S.trackRaw/S.trackAt（不在 sector.js）
    assert "invalidateTrack" in src and "S.trackRaw = null" in src and "S.trackAt = 0" in src
    assert "reloadTrack" in src
    # sector.js 一行不动：不得出现 invalidate 之类的出口
    sector = (FE / "sector.js").read_text(encoding="utf-8")
    assert "invalidate" not in sector, "sector.js 不该有 invalidate（缓存不在它身上）"


# ---------------------------------------------------------------- ② 与旧页对齐（刨掉主人改规格的两处）


@pytest.mark.skipif(not HAS_LEGACY, reason="缺旧页 engine/web/index.html（只读参考）")
def test_evo_html_matches_legacy_except_axis_and_rank() -> None:
    """4 组配置下，刨掉「曲线 path d / X 轴时间标签 / 右侧榜单块」后整段 HTML 逐字节相同。

    为什么要刨这三处（2026-10-26 主人改规格，不是走形）：
      · 曲线 path d + X 轴标签 —— 「滚轮缩放取消、固定时间轴」（X 从索引映射改为交易日分钟轴）；
      · 右侧榜单块 —— 「板块由8个增加为10个」。
    其余（图例 / 段控 / 回填按钮 / 预测浮层 / 标题 / 读数…）一律不许变。
    """
    out = _node(TESTS / "evo_align.mjs")
    assert out["total"] == 4
    bad = [c for c in out["cases"] if not c["sameStruct"]]
    assert not bad, "刨掉轴与榜单后仍与旧页不一致：%s" % json.dumps(
        [{"case": c["name"], "firstDiff": c["structDiff"]} for c in bad], ensure_ascii=False)


@pytest.mark.skipif(not HAS_LEGACY, reason="缺旧页 engine/web/index.html（只读参考）")
def test_evo_chart_data_identical_to_legacy() -> None:
    """曲线**数据**没走形：每张图 path 的 Y 序列逐点相同、data-n/data-off 相同、榜单/图例/段控相同。

    （X 允许不同：固定时间轴后 X 由时间算，旧页由索引算——见 test_evo_fixed_time_axis。）
    Y 的比法按模式分组（2026-10-30 补）：
      · amv 模式：像素 Y 逐字相同；
      · quote 模式：2026-10-28 把全A 基准线计入 Y 轴范围（N20261028-140428-39），像素必然不同，
        改比「同一仿射变换下逐点吻合」（同一组数値 ⇔ 两组 Y 只差一个斜率+截距）。
    """
    out = _node(TESTS / "evo_align.mjs")
    for c in out["cases"]:
        n = c["name"]
        assert c["yAffine"], "%s：曲线 Y 值不一致（数据走形）" % n
        assert c["pathModern"] == c["pathLegacy"] and c["pathModern"] > 0, "%s：path 条数" % n
        assert c["dataNAndOffCard"] == c["dataNAndOffLegacy"], "%s：data-n/data-off" % n
        assert c["svgCount"] == 2, "%s：必须两张图（左 %s + 右贡献）" % (n, c["evoKinds"][0])
        assert c["rankRows"] == c["rankRowsLegacy"] and c["rankRows"] > 0, "%s：榜单行数" % n
        assert c["rankText"] == c["rankTextLegacy"], "%s：榜单名次不一致" % n
        assert c["idxButtons"] == c["idxButtonsLegacy"] == 6, "%s：六指数开关" % n
        assert c["segLabels"] == c["segLabelsLegacy"], "%s：段控文案" % n
        # 2026-10-27（-29/-32）：quote 模式标题改成「行情涨跌 + 已选指数名」、不再挂 AMV 读数。
        # 所以 amvSub 按模式分组：amv 模式与旧页逐字比（活跃指数读数），quote 模式比标题 + 已选指数名。
        if c["mode"] == "quote":
            assert c["title"].strip() == "行情涨跌", "%s：行情模式标题：%r" % (n, c["title"])
            assert c["titleLegacy"].strip() == "活跃指数", "%s：旧页标题（对照）：%r" % (n, c["titleLegacy"])
            want = " / ".join(c["idxOnNames"]) or "六指数待选"
            assert c["amvSub"] == want, "%s：已选指数名：%r != %r" % (n, c["amvSub"], want)
        else:
            assert c["amvSub"] == c["amvSubLegacy"], "%s：活跃指数读数" % n


@pytest.mark.skipif(not HAS_LEGACY, reason="缺旧页 engine/web/index.html（只读参考）")
def test_evo_modes_switch_chart_kind() -> None:
    """左图两种模式各自出对的图：amv→data-evo=amv，quote→data-evo=quote；右图恒为 contrib。"""
    out = _node(TESTS / "evo_align.mjs")
    by = {c["name"]: c for c in out["cases"]}
    assert by["默认 amv/正/贡献"]["evoKinds"] == ["amv", "contrib"]
    assert by["行情涨跌+两指数"]["evoKinds"] == ["quote", "contrib"]


def test_evo_palette_shape() -> None:
    """调色板是 10 色 / 6 指数 6 色，且不重复（重复色会让两条线撞色分不清）。"""
    out = _expr("""
console.log(JSON.stringify({
  pal: E.EVO_PAL, idx: E.EVO_IDX, idxPal: E.EVO_IDX_PAL,
  chartH: E.EVO_CHART_H,
}));
""")
    assert len(out["pal"]) == 10 and len(set(out["pal"])) == 10
    assert len(out["idx"]) == 6 and len(out["idxPal"]) == 6 and len(set(out["idxPal"])) == 6
    assert out["idx"][0] == ["000001.SH", "上证指数"]
    assert out["idx"][-1] == ["399303.SZ", "国证2000"]
    assert out["chartH"] == 330, "左图 viewBox 高（旧页 4088 _evoChartH）"


def test_evo_empty_data_says_so() -> None:
    """没有轨迹 → 如实说「盘中 60s 采样后出现」，不画空图、不补零。"""
    out = _expr("""
const st = E.createState(null, null, { track: () => null, amv: () => null });
const h = E.html(st);
console.log(JSON.stringify({ h: h, svg: (h.match(/<svg/g) || []).length }));
""")
    assert "盘中 60s 采样后出现" in out["h"]
    assert out["svg"] == 0


def test_evo_gapbreak_inherited_from_caliber() -> None:
    """断线口径只许一份实现：evo 必须复用 caliber.lineD（不许自己再写一份断点判断）。"""
    src = EVO.read_text(encoding="utf-8")
    assert "from './caliber.js'" in src
    assert "lineD" in src
    assert "3 * 60 * 1000" not in src and "3*60*1000" not in src, \
        "evo.js 自己算了一遍 3 分钟断点（口径应只在 caliber.js）"


# ---------------------------------------------------------------- ③ 2026-10-26 主人三条新规格

# 交易日分钟轴梯子：09:30–11:30 + 13:00–15:00，共 240 分钟，午休压缩（11:30 与 13:00 共用同一个 X）
_FIXTURE_TIME = r"""
const t2s = (tm) => {
  const m = tm <= 120 ? 570 + tm : 780 + (tm - 120);
  return '2026-10-23 ' + String(Math.floor(m / 60)).padStart(2, '0') + ':'
    + String(m % 60).padStart(2, '0') + ':00';
};
const mkSnap = (tm, codes) => ({
  ts: t2s(tm), all: tm / 240,
  boards: Object.fromEntries(codes.map((c, i) => [c[0], {
    name: c[1], amv_pct: (tm / 240) * (i % 2 ? -1 : 1) * (10 - i * 0.5),
    chg_e: (i % 2 ? -1 : 1) * (3 + i * 0.2), chg_w: (i % 2 ? -1 : 1) * (2 + i * 0.1),
    amt_share: 3 + i * 2.5,
  }])),
  idx_chg: {},
});
const codes = Array.from({ length: CODE_N }, (_, i) => ['BK' + String(i + 1).padStart(3, '0'), '板块' + i]);
const weights = Object.fromEntries(codes.map((c, i) => [c[0], 3 + i * 2.5]));
const snaps = [];
for (let tm = 0; tm <= 240; tm += 2) snaps.push(mkSnap(tm, codes));
const track = { date: '20261023', weights_date: '20261022', weights: weights, snapshots: snaps, stale: false };
"""


def test_evo_fixed_time_axis() -> None:
    """主人：「固定时间轴，盘中不再拉伸K线。例如11点30K线就只能走到一半。」

    直接量像素：左图 ML=46、plotW=534（640−46−60），所以
      09:30 → 46｜11:30 → 46+267=313｜15:00 → 580。
    并要求 11:30 那个点**恰好落在半场**（不是被拉满宽度），且午休两端的 X 重合。
    """
    out = _expr(_FIXTURE_TIME.replace("CODE_N", "2") + """
const st = E.createState(null, null, { track: () => track, amv: () => null });
const h = E.html(st);
const svg = (h.match(/<svg[^>]*data-evo="amv"[\\s\\S]*?<\\/svg>/) || [''])[0];
const ds = (svg.match(/<path[^>]*\\sd="([^"]*)"/g) || []).map((s) => (s.match(/d="([^"]*)"/) || [])[1]);
const pairs = (d) => (d.match(/-?\\d+(?:\\.\\d+)?,-?\\d+(?:\\.\\d+)?/g) || []).map((s) => s.split(',').map(Number));
let best = [];
ds.forEach((d) => { const p = pairs(d); if (p.length > best.length) best = p; });
const ml = +(svg.match(/data-ml="(\\d+)"/) || ['', 0])[1];
const mr = +(svg.match(/data-mr="(\\d+)"/) || ['', 0])[1];
const w = +(svg.match(/data-w="(\\d+)"/) || ['', 0])[1];
console.log(JSON.stringify({
  pts: best.length, ml: ml, plotW: w - ml - mr,
  x0: best[0][0], xNoon: best[60][0], xAfter: best[61][0], xEnd: best[best.length - 1][0],
  monotonic: best.every((p, i) => i === 0 || p[0] >= best[i - 1][0]),
}));
""")
    assert out["pts"] == 121, "122 个采样点应全部入图（换了 shape 先看这里）"
    assert out["ml"] == 46 and out["plotW"] == 534
    assert abs(out["x0"] - 46) <= 0.2, "09:30 应贴左轴（实际 %.1f）" % out["x0"]
    assert abs(out["xNoon"] - 313) <= 0.6, "11:30 必须落在半场（实际 %.1f，应 313）" % out["xNoon"]
    assert abs(out["xEnd"] - 580) <= 0.6, "15:00 应贴右轴（实际 %.1f）" % out["xEnd"]
    assert abs(out["xAfter"] - out["xNoon"] - 534 * 2 / 240) <= 0.6, \
        "午休应压缩：跨午休只该宽出 2 个交易分钟（实际 %.1f，lunch 段宽度必须为 0）" % (
            out["xAfter"] - out["xNoon"])
    assert out["monotonic"], "X 必须单调递增（光标/区间依赖这个）"


def test_evo_rank_rows_are_ten() -> None:
    """主人：「板块由8个增加为10个」——正向/反向都是 10 行，且颜色不重复（EVO_PAL 恰好 10 色）。"""
    out = _expr(_FIXTURE_TIME.replace("CODE_N", "14") + """
const run = (dir) => {
  const st = E.createState(null, null, { track: () => track, amv: () => null });
  st.dir = dir; st.srt = 'amv';   // 按 AMV 排 → 顺序可预测（fixture：板块0 最高、板块1 最低）
  const h = E.html(st);
  const rows = (h.match(/class="evo-rank-row/g) || []).length;
  const names = (h.match(/<span class="evo-rank-name">([^<]*)<\\/span>/g) || [])
    .map((s) => s.replace(/<[^>]+>/g, ''));
  return { rows: rows, names: names };
};
console.log(JSON.stringify({
  pos: run('pos'), neg: run('neg'), all: run('all'), boards: 14,
}));
""")
    assert out["pos"]["rows"] == 10, "正向应 10 行（实际 %s）" % out["pos"]["rows"]
    assert out["neg"]["rows"] == 10
    assert out["all"]["rows"] == 10, "正负对比是 5+5"
    assert len(set(out["pos"]["names"])) == 10, "10 行不得重名"
    # 第一行必须真是最值的那个（fixture：板块0 最高、板块1 最低）
    assert out["pos"]["names"][0] == "板块0", "正向榜首取错：%s" % out["pos"]["names"][:3]
    assert out["neg"]["names"][0] == "板块1", "反向榜首取错：%s" % out["neg"]["names"][:3]


def test_evo_rank_click_goes_to_research_not_detail() -> None:
    """主人：「点击板块时，不再默认打开板块详情，而是跳到下方显示，同时板块排名内对应板块出现选中效果」。

    落点：
      · 右榜行只带 data-act="evo-goto"，**不带 data-open-amv**（带了宿主就会弹详情卡）；
      · evo.gotoBoard → hooks.pickBoard；
      · frontend.js 的 pickBoard = rsSet(sel) + 滚到研究区头；
      · research.js 的 rsSet('sel') 会把该板块行打上 `tr.pk.sel`（选中效果）并滚进可视区。
    """
    src = _code_only(EVO)
    assert 'data-act="evo-goto"' in src
    assert 'data-open-amv' not in src, "右榜还带着 data-open-amv（会弹板块详情）"
    assert "export function gotoBoard" in src
    assert "st.hooks && st.hooks.pickBoard" in src

    fe = FRONTEND.read_text(encoding="utf-8")
    assert "pickBoard:" in fe, "frontend.js 没接 evo 的 pickBoard"
    assert "research.rsSet(S.research, 'sel', code)" in fe
    assert "S.el.secResearch" in fe, "要滚到下方研究卡"

    rs = _code_only(FE / "research.js")
    assert "tr.pk.sel" in rs, "研究卡要能把选中行露出来"
    assert "box.scrollTop" in rs and "scrollIntoView" not in rs, \
        "只许滚左栏自己的 scrollTop（scrollIntoView 会把外层 stage 一并带走，把 evo 的跳转顶回去）"


def test_evo_wheel_zoom_and_shift_pan_removed() -> None:
    """主人：「这两张图表的滚轮缩放功能取消」——滚轮监听与 Shift 平移（都是改缩放比的）必须没。"""
    src = _code_only(EVO)
    assert "'wheel'" not in src, "滚轮监听还在（缩放没取消）"
    assert "shiftKey" not in src, "Shift 平移还在（会改缩放比）"
    assert "wheelLock" not in src, "90ms 滚轮节流该一并停用"
    # 固定时间轴之后不该再有「收窄窗口」的赋值（st.win 一旦被写，轴就不再固定）
    assert not re.search(r"st\.win\s*=", src), "还有地方在写 st.win（收窄即拉伸）"


def test_evo_all_info_row_absorbed_from_trace_card() -> None:
    """主人 2026-10-26：「这些数据可以吸收叠加到活跃市值的图上」——
    原【板块轨迹明细】卡的两行摘要搬到左图做信息行，7 个数字要逐项对上，且**不许重算口径**：

      全A 起点 / 末值 / 日内变化 / 跑赢·跑输  → 直取 summary 字段（all_delta 不复算）
      时段 / 峰值 / 谷值               → 由 all_series 算（与删之前的 trackSpark 同算法）
    """
    out = _expr(_FIXTURE_TIME.replace("CODE_N", "2") + """
const summary = {
  ok: true, date: '20261023', last_ts: '2026-10-23 15:00:00', snaps: 235, boards_total: 90,
  first_all: -1.42, last_all: 5.08, all_delta: 6.49, strong: 27, weak: 63,
  all_series: [
    { t: '09:30', all: -1.42 }, { t: '10:30', all: -2.00 },
    { t: '13:30', all: 5.14 }, { t: '14:00', all: 3.20 }, { t: '15:00', all: 5.08 },
  ],
};
const render = (sm) => {
  const st = E.createState(null, null, { track: () => track, amv: () => null, summary: () => sm });
  return E.html(st);
};
const h = render(summary);
const row = (h.match(/<div class="evo-info">[\\s\\S]*?<\\/div>/) || [''])[0];
const txt = row.replace(/<[^>]+>/g, ' ').replace(/\\s+/g, ' ').trim();
console.log(JSON.stringify({
  hasRow: !!row, text: txt,
  noSummary: (render(null).match(/class="evo-info"/g) || []).length,
  order: (h.indexOf('class="evo-info"') > h.indexOf('</svg>') &&
          h.indexOf('class="evo-info"') < h.indexOf('class="evo-lg')),
}));
""")
    assert out["hasRow"] is True, "左图没有信息行"
    t = out["text"]
    assert "-1.42%" in t, "全A 起点：%s" % t
    assert "+5.08%" in t, "末值"
    assert "+6.49pp" in t, "日内变化（直取 all_delta，不自己减）"
    assert "27 / 63" in t, "跑赢 / 跑输"
    assert "09:30 → 15:00" in t, "时段（取 all_series 首末 t）"
    assert "5.14%" in t, "峰值（all_series 最大值）"
    assert "-2.00%" in t, "谷值（all_series 最小值）"
    assert out["noSummary"] == 0, "没数据时不该占位（不拿 0 冒充）"
    assert out["order"] is True, "信息行应在图下、图例（.evo-lg）上方"
