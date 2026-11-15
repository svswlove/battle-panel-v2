# -*- coding: utf-8 -*-
"""盘中口径层（frontend/caliber.js + format.js）的守门测试。

为什么要这一层：`_st*` / `_evo*` 全搬自旧页 engine/web/index.html，
里面每一条口径（盘中时段过滤、rel 超额、Δ占比‰、断点断线）都是历史踩坑攒出来的。
搬错了不会报错，只会显示错的数——这种缺陷只能靠断言抓。

用 Node 加载真实模块跑断言（与 sector 卡 test_sector_compute.py 同一做法）：
测的是真代码，不是源码里的字符串。
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

PANEL = Path(__file__).resolve().parents[4]        # projects/panel-v2
PLUGIN = PANEL / "app" / "plugins" / "intraday"
FE = PLUGIN / "frontend"


def _run_node(script: str) -> dict:
    full = (
        "const F = await import('file:///%s');\n" % (FE / "format.js").as_posix()
        + "const C = await import('file:///%s');\n" % (FE / "caliber.js").as_posix()
        + script
    )
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", full],
        capture_output=True, text=True, timeout=120, cwd=str(PANEL),
    )
    if proc.returncode != 0:
        pytest.fail("node 执行失败：\n%s\n%s" % (proc.stdout[-2000:], proc.stderr[-2000:]))
    return json.loads(proc.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------- 静态

def test_caliber_is_pure_module() -> None:
    """口径层不得碰 DOM / 网络 / 存储 —— 它是可单测的纯函数集合。"""
    src = (FE / "caliber.js").read_text(encoding="utf-8")
    for banned in ("document.", "window.", "fetch(", "localStorage", "getElementById"):
        assert banned not in src, "caliber.js 不应出现 %s" % banned


def test_format_is_pure_module() -> None:
    src = (FE / "format.js").read_text(encoding="utf-8")
    for banned in ("document.", "window.", "fetch(", "localStorage"):
        assert banned not in src, "format.js 不应出现 %s" % banned


def test_no_hardcoded_hex_in_caliber_and_format() -> None:
    """口径/格式化层不涉及颜色。"""
    for name in ("caliber.js", "format.js"):
        assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", (FE / name).read_text(encoding="utf-8")), name


def test_session_bounds_match_legacy() -> None:
    """旧页 4782 注释原话：只取 09:30–15:00，剔盘前 08:xx 与收盘后点。"""
    src = (FE / "caliber.js").read_text(encoding="utf-8")
    assert "export const SESSION_OPEN = '09:30';" in src
    assert "export const SESSION_CLOSE = '15:00';" in src


# ---------------------------------------------------------------- 时段过滤

def test_snaps_filters_out_of_session() -> None:
    out = _run_node("""
const track = { snapshots: [
  { ts: '2026-10-23 08:59:10', all: -9.9, boards: {} },
  { ts: '2026-10-23 09:29:59', all: -5.0, boards: {} },
  { ts: '2026-10-23 09:30:00', all: 1.0, boards: {} },
  { ts: '2026-10-23 15:00:00', all: 2.0, boards: {} },
  { ts: '2026-10-23 15:00:56', all: 3.0, boards: {} },
  { ts: '2026-10-23 15:30:00', all: 9.9, boards: {} },
]};
console.log(JSON.stringify({
  ts: C.snapsOf(track).map(s => s.ts.slice(11,16)),
  latest: C.latestOf(track).all,
}));
""")
    # 15:00:56 的 slice(11,16) = '15:00' → 在区间内（与旧页同口径，不按秒判）
    assert out["ts"] == ["09:30", "15:00", "15:00"], out
    assert out["latest"] == 3.0, out


def test_snaps_empty_when_all_off_session() -> None:
    out = _run_node("""
const track = { snapshots: [{ ts: '2026-10-23 08:59:00', all: 1, boards: {} }] };
console.log(JSON.stringify({ n: C.snapsOf(track).length, latest: C.latestOf(track) }));
""")
    assert out["n"] == 0 and out["latest"] is None, out


# ---------------------------------------------------------------- 四档口径

TRACK = {
    "weights": {"A": 10.0, "B": 5.0},
    "snapshots": [
        {"ts": "2026-10-23 09:30:00", "all": 0.0,
         "boards": {"A": {"name": "甲", "amv_pct": 1.0, "amt_share": 11.0, "chg_e": 0.5, "chg_w": 0.4}}},
        {"ts": "2026-10-23 14:00:00", "all": 2.0,
         "boards": {"A": {"name": "甲", "amv_pct": 5.0, "amt_share": 12.0, "chg_e": 3.0, "chg_w": 2.5}}},
    ],
}


def test_rel_is_amv_minus_all() -> None:
    """旧页 4784：rel = 板块 amv_pct − 同快照 all（超额强度）。"""
    out = _run_node("""
const track = %s;
const s = C.snapsOf(track)[1];
console.log(JSON.stringify({ rel: C.valOf(track, s, 'A', 'rel'), abs: C.valOf(track, s, 'A', 'abs'),
                             amt: C.valOf(track, s, 'A', 'amt') }));
""" % json.dumps(TRACK))
    assert out["rel"] == pytest.approx(3.0), "5.0 - 2.0"
    assert out["abs"] == pytest.approx(5.0)
    assert out["amt"] == pytest.approx(12.0)


def test_contrib_is_delta_share_in_per_mille() -> None:
    """旧页 4917：contrib = (当前占比 − EOD 基线占比) × 10，单位 ‰。

    当前占比 = 基线 × (1+amv/100) / (1+all/100)
    这里：10 × (1.05) / (1.02) = 10.294… → Δ = 0.294…% → ×10 = 2.94‰
    """
    out = _run_node("""
const track = %s;
const s = C.snapsOf(track)[1];
console.log(JSON.stringify({ share: C.shareOf(track, 'A', s), contrib: C.contribOf(track, 'A', s) }));
""" % json.dumps(TRACK))
    assert out["share"] == pytest.approx(10 * 1.05 / 1.02, rel=1e-9)
    assert out["contrib"] == pytest.approx((10 * 1.05 / 1.02 - 10) * 10, rel=1e-9)


def test_contrib_missing_weight_is_null_not_zero() -> None:
    """缺基线权重必须返回 null（缺数据就标缺），不能当 0 算成「无贡献」。"""
    out = _run_node("""
const track = %s;
const s = C.snapsOf(track)[1];
console.log(JSON.stringify({ contrib: C.contribOf(track, 'ZZZ', s),
                             val: C.valOf(track, s, 'ZZZ', 'contrib') }));
""" % json.dumps(TRACK))
    assert out["contrib"] is None and out["val"] is None, out


def test_chg_val_weighted_vs_equal() -> None:
    out = _run_node("""
const track = %s;
const s = C.snapsOf(track)[1];
console.log(JSON.stringify({ e: C.chgValOf(s, 'A', 'e'), w: C.chgValOf(s, 'A', 'w') }));
""" % json.dumps(TRACK))
    assert out["e"] == pytest.approx(3.0) and out["w"] == pytest.approx(2.5)


def test_unit_and_label_per_caliber() -> None:
    out = _run_node("""
console.log(JSON.stringify({ rel: C.unitOf('rel'), contrib: C.unitOf('contrib'), abs: C.unitOf('abs'),
                             amt: C.unitOf('amt'),
                             l1: C.labelOf(1.234, 'rel'), l2: C.labelOf(-1.234, 'contrib'),
                             l3: C.labelOf(null, 'rel') }));
""")
    assert out == {"rel": "pp", "contrib": "‰", "abs": "%", "amt": "%",
                   "l1": "+1.23pp", "l2": "-1.23‰", "l3": "—"}, out


# ---------------------------------------------------------------- 断点断线

def test_gap_break_three_minutes() -> None:
    """旧页 4066：相邻点 > 3 分钟即断更（10:38→15:07 不能连成假跳变）。

    阈值是**严格大于** 3 分钟：整 3 分整不断，3 分 01 秒断。
    """
    out = _run_node("""
const snaps = [
  { ts: '2026-10-23 10:00:00' },
  { ts: '2026-10-23 10:01:00' },   // 1 分：不断
  { ts: '2026-10-23 10:04:00' },   // 3 分整：不断
  { ts: '2026-10-23 10:07:01' },   // 3 分 01 秒：断
  { ts: '2026-10-23 15:07:00' },   // 大断
];
console.log(JSON.stringify([0,1,2,3,4].map(i => C.gapBreak(snaps, i))));
""")
    assert out == [False, False, False, True, True], out


def test_line_d_breaks_at_gap_and_null() -> None:
    """断更与缺值都必须产生新的 M（断线），不能跨过去画一条直线。"""
    out = _run_node("""
const snaps = [
  { ts: '2026-10-23 10:00:00' }, { ts: '2026-10-23 10:01:00' },
  { ts: '2026-10-23 10:02:00' }, { ts: '2026-10-23 15:07:00' },
  { ts: '2026-10-23 15:08:00' },
];
const vals = [1, 2, null, 4, 5];
const X = i => i * 10, Y = v => 100 - v;
console.log(JSON.stringify({ d: C.lineD(snaps, vals, X, Y) }));
""")
    d = out["d"]
    # 1,2 连成 M...L...；null 后 4 重新 M；4→5 之间是 15:07→15:08 1 分钟，应连 L
    assert d.startswith("M0.0,99.0 L10.0,98.0"), d
    assert " M30.0,96.0" in d, d
    assert d.endswith("L40.0,95.0"), d


# ---------------------------------------------------------------- 格式化

def test_format_matches_legacy_semantics() -> None:
    out = _run_node("""
console.log(JSON.stringify({
  num_null: F.num(null), num_nan: F.num(NaN), num_d: F.num(1.2345, 3),
  pct_pos: F.pct(1.2), pct_neg: F.pct(-1.2), pct_null: F.pct(null),
  pp_pos: F.pp(0.5),
  cls_up: F.clsUp(1), cls_down: F.clsUp(-1), cls_zero: F.clsUp(0), cls_null: F.clsUp(null),
  cls_it: F.clsIt(1),
  code6: F.code6('600519.SH'), code6_null: F.code6(null),
  amt_yi: F.fmtAmount(3.5e8), amt_wy: F.fmtAmount(2.2e12), amt_null: F.fmtAmount(null),
  d_short: F.dShort('20261023'), d_raw: F.dShort('2026-10-23'),
  esc: F.esc('<b a="x">&\\'</b>'),
}));
""")
    assert out["num_null"] == "—" and out["num_nan"] == "—"
    assert out["num_d"] == "1.234" or out["num_d"] == "1.235", out["num_d"]  # toFixed 舍入
    assert out["pct_pos"] == "+1.20%" and out["pct_neg"] == "-1.20%" and out["pct_null"] == "—"
    assert out["pp_pos"] == "+0.50pp"
    assert out["cls_up"] == "up" and out["cls_down"] == "down"
    assert out["cls_zero"] == "" and out["cls_null"] == ""
    assert out["cls_it"] == "it-up"
    assert out["code6"] == "600519" and out["code6_null"] == ""
    assert out["amt_yi"] == "3.5 亿" and out["amt_wy"] == "2.20 万亿" and out["amt_null"] == "—"
    assert out["d_short"] == "10/23" and out["d_raw"] == "2026-10-23"
    assert out["esc"] == "&lt;b a=&quot;x&quot;&gt;&amp;&#39;&lt;/b&gt;"


# ---------------------------------------------------------------- 旧页对齐

def _run_node_file(script: str) -> dict:
    """把脚本写成临时文件跑。

    为什么不能走 `-e`：真实轨迹 2.7MB，内联进命令行会超 Windows 命令长度上限
    （实测 WinError 206）。这里让 node 自己读文件。
    """
    import tempfile
    full = (
        "const F = await import('file:///%s');\n" % (FE / "format.js").as_posix()
        + "const C = await import('file:///%s');\n" % (FE / "caliber.js").as_posix()
        + "const fs = await import('node:fs');\n"
        + script
    )
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as fh:
        fh.write(full)
        tmp = fh.name
    try:
        proc = subprocess.run(
            ["node", tmp], capture_output=True, text=True, timeout=180, cwd=str(PANEL),
        )
        if proc.returncode != 0:
            pytest.fail("node 执行失败：\n%s\n%s" % (proc.stdout[-2000:], proc.stderr[-2000:]))
        return json.loads(proc.stdout.strip().splitlines()[-1])
    finally:
        try:
            Path(tmp).unlink()
        except OSError:
            pass


def test_caliber_matches_legacy_on_real_track() -> None:
    """拿真实生产轨迹（若存在）跑一遍，把关键数字与手算值对上。

    这条不是"看起来对"：它把 09:30 那一点的 rel 与「amv_pct − all」逐个板块比。
    """
    panel = PANEL.parents[1] / "data" / "panel" / "amv_live_sector_track.json"
    if not panel.is_file():
        pytest.skip("缺生产轨迹文件 data/panel/amv_live_sector_track.json")
    doc = json.loads(panel.read_text(encoding="utf-8"))
    dates = sorted((doc.get("dates") or {}).keys())
    if not dates:
        pytest.skip("轨迹文件无任何日期")
    picked = dates[-1]
    snaps = [s for s in (doc["dates"][picked] or [])
             if "09:30" <= str(s.get("ts") or "")[11:16] <= "15:00"]
    if not snaps:
        pytest.skip("该日无盘中快照")
    track = {"snapshots": snaps, "weights": doc.get("weights") or {}}
    script = """
const track = JSON.parse(fs.readFileSync(%s, 'utf8'));
const snaps = C.snapsOf(track);
const last = snaps[snaps.length-1];
const codes = Object.keys(last.boards || {}).slice(0, 12);
const rows = codes.map(c => ({ c,
  rel: C.valOf(track, last, c, 'rel'),
  expect: (last.boards[c].amv_pct == null || last.all == null) ? null : last.boards[c].amv_pct - last.all,
  contrib: C.contribOf(track, c, last),
}));
console.log(JSON.stringify({ n: snaps.length, first: snaps[0].ts, last: last.ts,
                              lastAll: last.all, rows }));
""" % json.dumps(str(panel).replace("\\", "/"))
    # 直接读生产文件后按旧页口径自己筛一遍（不依赖后端 summary，避免测成「摘要端点对不对」）
    script = """
const doc = JSON.parse(fs.readFileSync(%s, 'utf8'));
const dates = Object.keys(doc.dates || {}).sort();
const picked = dates[dates.length-1];
const raw = doc.dates[picked] || [];
const snaps = raw.filter(s => { const t = String(s.ts||'').slice(11,16); return t >= '09:30' && t <= '15:00'; });
const track = { snapshots: snaps, weights: doc.weights || {} };
const last = C.latestOf(track);
const codes = Object.keys(last.boards || {}).slice(0, 12);
const rows = codes.map(c => ({ c,
  rel: C.valOf(track, last, c, 'rel'),
  expect: (last.boards[c].amv_pct == null || last.all == null) ? null : last.boards[c].amv_pct - last.all,
  contrib: C.contribOf(track, c, last),
  seriesN: C.seriesOf(track, c, 'rel').length,
}));
console.log(JSON.stringify({ picked, n: snaps.length, total: raw.length,
                              first: snaps[0].ts, last: last.ts, lastAll: last.all, rows }));
""" % json.dumps(str(panel).replace("\\", "/"))
    out = _run_node_file(script)
    assert out["n"] <= out["total"], "过滤后点数不应多于原始点"
    assert out["n"] > 0
    for r in out["rows"]:
        if r["expect"] is None:
            assert r["rel"] is None, r
        else:
            assert r["rel"] == pytest.approx(r["expect"], rel=1e-9), r
        assert r["seriesN"] == out["n"], "序列长度必须等于盘中点数"
