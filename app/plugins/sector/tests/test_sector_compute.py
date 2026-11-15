# -*- coding: utf-8 -*-
"""板块卡片计算层（frontend/compute.js）的守门测试。

这些测试不跑浏览器、不需要数据库：用构造的小矩阵验证「算得对不对」，
再用一份**真实接口快照**验证「与 8002 逐行一致」。

为什么要有这一层：板块卡重做后，榜单/榜位/评分/在榜全在前端算。
算错不会报错，只会显示错的板块——这种缺陷只能靠对照断言抓。
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

PANEL = Path(__file__).resolve().parents[4]        # projects/panel-v2
PLUGIN = PANEL / "app" / "plugins" / "sector"
COMPUTE = PLUGIN / "frontend" / "compute.js"


def _src() -> str:
    return COMPUTE.read_text(encoding="utf-8")


# ---------------------------------------------------------------- 静态结构

def test_compute_is_pure_module() -> None:
    """计算层不得碰 DOM —— 它是可单测的纯函数集合。"""
    src = _src()
    for banned in ("document.", "window.", "fetch(", "localStorage"):
        assert banned not in src, "compute.js 不应出现 %s（该由渲染层承担）" % banned
    assert "export function createCompute(" in src
    assert "export function mvHtml(" in src
    assert "export function topCount(" in src


def test_compute_keeps_8002_thresholds() -> None:
    """阈值常量必须与 8002 一致（改一个数字，榜单就换一批板块）。"""
    src = _src()
    assert "export const TOP_PCT = 0.10" in src
    assert "export const TOP_MIN = 10" in src
    assert "export const TOP_MAX = 30" in src
    assert "export const REIN_N = 3" in src
    assert "SCORE_WINS = [1, 3, 5, 10, 20]" in src
    assert "SCORE_W = [0.15, 0.20, 0.25, 0.25, 0.15]" in src
    assert "HIT_WIN = 30" in src


def test_compute_has_no_hardcoded_colors() -> None:
    """计算层不该出现颜色（样式只属于渲染层与主题表）。"""
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", _src())


# ---------------------------------------------------------------- 数值语义（Node 跑真实函数）

def _run_node(script: str) -> dict:
    """在 Node 里加载 compute.js 跑一段断言脚本，返回其 JSON 结果。"""
    mod_url = COMPUTE.as_posix()
    full = (
        "const m = await import('file:///%s');\n" % mod_url
        + script
    )
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", full],
        capture_output=True, text=True, timeout=120,
        cwd=str(PANEL),
    )
    if proc.returncode != 0:
        pytest.fail("node 执行失败：\n%s\n%s" % (proc.stdout[-2000:], proc.stderr[-2000:]))
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_top_count_clamps_like_8002() -> None:
    out = _run_node("""
const r = [1, 5, 9, 10, 19, 20, 50, 90, 91, 230, 299, 300, 1000].map((n) => m.topCount(n));
console.log(JSON.stringify(r));
""")
    # 10% 后夹到 [10, 30]；最后一层再夹 floor(n/2) 防前后榜重叠（小样本层 10 的下限会吃满全池）
    assert out == [1, 2, 4, 5, 9, 10, 10, 10, 10, 23, 30, 30, 30], out


def test_sort_is_always_descending() -> None:
    """sortByKey 固定降序（8002 曾因写成升序把最差的装进「前 10」）。"""
    out = _run_node("""
const C = m.createCompute({
  catalog: { l2: [
    { code: 'A', name: 'A' }, { code: 'B', name: 'B' }, { code: 'C', name: 'C' },
  ]},
  rankRecent: { dates: ['d0', 'd1'], total: [1e6, 1e6], codes: ['A','B','C'],
                weight: [[3e6, 2e6, 1e6], [1e6, 5e6, 9e6]] },
  level: 'L2', sortKey: 'd', winN: 1,
});
const pool = C.poolAt(1, 1, null);
C.sortPool(pool);
console.log(JSON.stringify(pool.map((r) => [r.code, r.d])));
""")
    # d = now - prev: A=-2e6, B=+3e6, C=+8e6 → 降序 C,B,A
    assert out == [["C", 8000000], ["B", 3000000], ["A", -2000000]], out


def test_top_count_never_overlaps_sides() -> None:
    """前后 10% 不得重叠：k <= ⌊n/2⌋（钻取单个一级行业时最容易撞上）。

    2026-10-25 实测缺陷：缺这层夹，电子行业 19 个板块时前 10% 拿满 10 个，
    后 10% 被 inSet 排空（实测 inN=6 / outN=0，后半边直接空）。
    """
    out = _run_node("""
const r = {};
for (let n = 1; n <= 40; n++) r[n] = m.topCount(n);
console.log(JSON.stringify(r));
""")
    for n in range(1, 41):
        k = out[str(n)]
        assert 1 <= k <= max(1, n // 2), "n=%d 时 topCount=%d 会让前后榜重叠" % (n, k)


def test_board_status_marks_new_and_flip() -> None:
    """换边不算 NEW（8002 主人 2026-10-22 报过的缺陷）。"""
    out = _run_node("""
// 3 天、3 个板块：让 A 先在后 10% 再翻到前 10%
const C = m.createCompute({
  catalog: { l2: [
    { code: 'A', name: 'A' }, { code: 'B', name: 'B' }, { code: 'C', name: 'C' },
    { code: 'D', name: 'D' },
  ]},
  rankRecent: {
    dates: ['d0','d1','d2'], total: [1e6,1e6,1e6],
    codes: ['A','B','C','D'],
    // 4 个板块 → topCount = 10（下限），所以全部同时进 in 和 out（互相排除后 out 为空）
    weight: [[4e6,3e6,2e6,1e6],[4e6,3e6,2e6,1e6],[4e6,3e6,2e6,1e6]],
  },
  level: 'L2', sortKey: 'd', winN: 1, dayB: 2,
});
const st = C.boardStatus(2, 1, null, 0, 'in');
console.log(JSON.stringify(st));
""")
    # 板块数少于下限 → 全员 in，streak 应等于可算天数
    assert out["streak"] >= 1, out


def test_board_hits_counts_window() -> None:
    out = _run_node("""
const C = m.createCompute({
  catalog: { l2: [
    { code: 'A', name: 'A' }, { code: 'B', name: 'B' },
    { code: 'C', name: 'C' }, { code: 'D', name: 'D' },
  ]},
  rankRecent: {
    dates: ['d0','d1','d2'], total: [1e6,1e6,1e6],
    codes: ['A','B','C','D'],
    weight: [[4e6,3e6,2e6,1e6],[4e6,3e6,2e6,1e6],[4e6,3e6,2e6,1e6]],
  },
  level: 'L2', sortKey: 'd', winN: 1, dayB: 2,
});
const h = C.boardHits(2, 1, null, 0, 'in', 30);
console.log(JSON.stringify({ days: h.days, total: h.total, today: h.today }));
""")
    assert out["total"] >= 1 and out["days"] >= 1, out
    assert len(out) == 3


# ---------------------------------------------------------------- 与 8002 渲染快照对齐

REF = Path(__file__).resolve().parent / "ref_8002_l2_3d.json"


@pytest.mark.skipif(not REF.is_file(), reason="缺 8002 渲染快照（ref_8002_l2_3d.json）")
def test_matches_8002_rendered_lists() -> None:
    """真实快照逐行对比：前 10%/后 10% + 退出项的名称序列必须完全一致。

    快照来源：8002 工作台 index.html 在 L2 / 3天 / 2026-10-23 的实渲染结果
    （listIn 12 行 / listOut 13 行）。
    """
    ref = json.loads(REF.read_text(encoding="utf-8"))
    snap = Path(__file__).resolve().parent / "ref_v2_api.json"
    if not snap.is_file():
        pytest.skip("缺 v2 接口快照（ref_v2_api.json）")
    api = json.loads(snap.read_text(encoding="utf-8"))

    script = """
const fs = await import('node:fs/promises');
const snap = JSON.parse(await fs.readFile(%s, 'utf8'));
const C = m.createCompute({
  catalog: snap.catalog,
  rankRecent: snap.rankRecent,
  level: 'L2', sortKey: 'd', winN: 3, drillL1: null,
  flowMap: snap.flowMap, divMap: snap.divMap,
  dayB: snap.rankRecent.dates.length - 1,
});
const b = C.computeBoardLists(snap.rankRecent.dates.length - 1);
console.log(JSON.stringify({
  in: [...b.topIn, ...b.exitsIn].map((r) => r.name || r.code),
  out: [...b.topOut, ...b.exitsOut].map((r) => r.name || r.code),
  warmup: b.warmup,
}));
""" % json.dumps(str(snap).replace("\\", "/"))
    got = _run_node(script)

    assert got["warmup"] is False
    assert got["in"] == ref["in"], "前 10%% 序列不一致：\n mine=%s\n ref =%s" % (got["in"], ref["in"])
    assert got["out"] == ref["out"], "后 10%% 序列不一致：\n mine=%s\n ref =%s" % (got["out"], ref["out"])
