# -*- coding: utf-8 -*-
r"""amv_contrib.py · 活跃市值贡献分项（口径唯一源，2026-10-18）

主人原话：「不知道活跃市值的上涨是由换手驱动的，还是由涨幅驱动的」——本模块就是回答这个。

口径（唯一源，禁第二份实现）：
    AMV = 不复权收盘价 p × 活跃流通股本 alive（alive 是 v9 死筹源递推的产物，单位=股）
    两日之间的 ΔAMV 逐股拆成五项，**恒等于 ΔAMV**（逐股恒等式，见 §0 自检）：

      涨跌贡献   = p1·a1 − p0·a1                    价格动 / 「涨幅驱动」
      流通股贡献 = p0·a0 · (sh1/sh0 − 1)            股本动：解禁、送转
      活跃度贡献 = p0·a0 · (a1/a0 − sh1/sh0)        换手动 / 「换手驱动」
      新股上市   = d0 无 AMV、d1 有 → 全额计入
      停牌/缺数 = d1 无 AMV → −AMV0（v9 当日 contrib=0 的语义）

    转成面板口径（‰）后，再加一档「大盘基准」——Δ占比 换分母带来的效应：
      指数贡献‰ = Δ占比 = ΣAMV(d1)/T1 − ΣAMV(d0)/T0，再 ×1000
      五项合计 + 大盘基准（−AMV0·r_all/T1）= 指数贡献（逐分钱对得上）

数据源（只读，不重算 AMV、不落盘）：
    · amv_board.db::stock_amv（逐股 AMV 日序，v9 递推产物）
    · amv_board.db::stock_state（alive/sh，库内仅近 31 日 ⇒ 分项最多回溯 ~30 个交易日）
    · amv_board.db::daily_total（分母）

北交：板块/大盘聚合按 bj_scope 剔北交（与 amv_live 聚合 mask 同源）；`board_parts(ex_bj=True)`
默认剔，与 sector_daily 的板块值同口径 ⇒ Σ成分股 == 板块。

调用方：`projects/panel-v2/app/lib/amv_contrib.py`（8810 sector 插件）与本模块同口径共用；
        旧 8002（`夏天-activity/工具/dashboard/server.py`）已于 2026-10-30 下线并销毁，不再列入。
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:  # 北交唯一源；缺失时退化为不剔（不阻断计算，但会在结果里标出）
    import bj_scope as _bj  # noqa: E402
except Exception:  # noqa: BLE001
    _bj = None

PART_KEYS = ("px", "sh", "act", "new", "susp")
PART_NAMES = {"px": "涨跌贡献", "sh": "流通股贡献", "act": "活跃度贡献",
              "new": "新股上市贡献", "susp": "停牌/缺数"}


def parts(x0, x1, a0, sh0, a1, sh1):
    """逐股五档分项（元）——五项加总恒等于 x1 − x0。

    x* = 该股 AMV（元）；a* = alive（股）；sh* = 流通股本（股）。缺值传 None。
    边界：d0 无 AMV、d1 有 → 全额「新股」；d1 无 AMV、d0 有 → 全额「停牌/缺数」。
    """
    if x0 <= 0 and x1 > 0:
        return 0.0, 0.0, 0.0, x1, 0.0
    if x1 <= 0 and x0 > 0:
        return 0.0, 0.0, 0.0, 0.0, -x0
    if x0 <= 0 and x1 <= 0:
        return 0.0, 0.0, 0.0, 0.0, 0.0
    ra = (a1 / a0) if (a0 and a0 > 0 and a1 and a1 > 0) else 1.0
    rsh = (sh1 / sh0) if (sh0 and sh0 > 0 and sh1 and sh1 > 0) else 1.0
    return (x1 - x0 * ra,            # 涨跌：p1·a1 − p0·a1
            x0 * (rsh - 1.0),        # 流通股：p0·a0·Δsh/sh
            x0 * (ra - rsh),         # 活跃度：p0·Δa 剔掉股本变动影响
            0.0, 0.0)


def _is_bj(code, fn):
    try:
        return bool(fn(code))
    except Exception:  # noqa: BLE001
        return False


def board_parts(conn, codes, d0, d1, ex_bj=True, bj_fn=None):
    """一组股票（=一个板块/概念/指数成分）在 d0→d1 窗口内的贡献分项 → dict。

    **多日窗口按逐日链式累加**（2026-10-18 主人要「分项跟周期跑」时重写）：
      每日用当日权重拆一次（parts 元），再逐日相加。不采用「两端一次跳」——
      后者把权重算在**期末 alive** 上，长窗口（3/5/10/20 日）会把涨跌/活跃度的归属搞失真
      （实测军工装备 3 日：一次跳得 AMV 加权价格效应 −1.28%，而逐日链式与等权/板块指数同号）。
      链式的加总仍严格等于 ΔAMV（逐日 telescoping），所以口径不变。

    → {'px','sh','act','new','susp','den','total','cover'}（‰，相对全市场期末总盘 T1）；
      另附 'amv0','amv1','T0','T1','n','n_bj','d0','d1','days'（原始量，供核对/展示）。
    读不到基准（daily_total 缺日/无成分）→ None。
    """
    t = conn.execute("SELECT date,total FROM daily_total WHERE date IN (?,?)", (d0, d1)).fetchall()
    tot = {r[0]: float(r[1]) for r in t if r[1]}
    T0, T1 = tot.get(d0), tot.get(d1)
    if not T0 or not T1 or not codes:
        return None
    ds = [r[0] for r in conn.execute(
        "SELECT date FROM daily_total WHERE date>=? AND date<=? ORDER BY date", (d0, d1))]
    if len(ds) < 2 or ds[0] != d0 or ds[-1] != d1:
        ds = [d0, d1]                      # 日历缺口/脏数据 → 退化为两端一步（不编造中间日）
    fn = bj_fn or (getattr(_bj, "is_bj", None) if _bj else None)
    keep = [str(c) for c in codes if not (ex_bj and fn and _is_bj(c, fn))]
    if not keep:
        return None
    want = set(keep)
    n_bj = len([c for c in codes if ex_bj and fn and _is_bj(c, fn)]) if fn else 0
    # 一次拉齐窗口内逐日 AMV 与 alive/sh（成分≤400 时带 stock IN 限行；全A 级只按日扫）
    q_amv = "SELECT date,stock,amv FROM stock_amv WHERE date>=? AND date<=?"
    q_st = "SELECT date,stock,alive,sh FROM stock_state WHERE date>=? AND date<=?"
    pars = [ds[0], ds[-1]]
    if len(keep) <= 400:
        ph = ",".join("?" * len(keep))
        q_amv += " AND stock IN (" + ph + ")"
        q_st += " AND stock IN (" + ph + ")"
        pars = [ds[0], ds[-1]] + keep
    amv, stt = {}, {}                      # amv[(d,stock)] / stt[(d,stock)]
    for d, s, v in conn.execute(q_amv, tuple(pars)):
        amv[(d, str(s))] = (v or 0.0)
    for d, s, al, sh in conn.execute(q_st, tuple(pars)):
        stt[(d, str(s))] = (al, sh)
    acc = [0.0] * 5
    s0 = s1 = 0.0
    for i in range(len(ds) - 1):
        a, b = ds[i], ds[i + 1]
        for c in keep:
            x0, x1 = amv.get((a, c), 0.0), amv.get((b, c), 0.0)
            if x0 <= 0 and x1 <= 0:
                continue
            a0, sh0 = stt.get((a, c)) or (None, None)
            a1, sh1 = stt.get((b, c)) or (None, None)
            p = parts(x0, x1, a0, sh0, a1, sh1)
            for k in range(5):
                acc[k] += p[k]
            if i == 0:
                s0 += x0
            if i == len(ds) - 2:
                s1 += x1
    r_all = T1 / T0 - 1.0
    out = {k: acc[i] / T1 * 1000.0 for i, k in enumerate(PART_KEYS)}
    out["den"] = -s0 * r_all / T1 * 1000.0
    out["total"] = (s1 / T1 - s0 / T0) * 1000.0
    out["cover"] = sum(out[k] for k in PART_KEYS) + out["den"]
    out.update({"amv0": s0, "amv1": s1, "T0": T0, "T1": T1,
                "n": len(keep), "n_bj": n_bj, "d0": d0, "d1": d1, "days": len(ds) - 1,
                "all_pct": r_all * 100.0})
    return out


def _selftest():
    """§0 自检：逐股恒等式 8 个分支（含新股/停牌/缺 alive/股本增减/alive 翻倍）。"""
    cases = [
        ("普通", 100.0, 110.0, 10.0, 10.0, 12.0, 10.0),
        ("新股", 0.0, 50.0, None, None, 10.0, 10.0),
        ("停牌", 80.0, 0.0, 10.0, 10.0, 10.0, 10.0),
        ("双无", 0.0, 0.0, None, None, None, None),
        ("缺 alive", 100.0, 120.0, None, None, None, None),
        ("股本扩张", 100.0, 100.0, 10.0, 10.0, 10.0, 20.0),
        ("股本缩减+价涨", 200.0, 260.0, 10.0, 20.0, 10.0, 10.0),
        ("alive 翻倍", 100.0, 100.0, 10.0, 10.0, 20.0, 10.0),
    ]
    bad = 0
    for name, x0, x1, a0, sh0, a1, sh1 in cases:
        s = sum(parts(x0, x1, a0, sh0, a1, sh1))
        ok = abs(s - (x1 - x0)) < 1e-9
        bad += 0 if ok else 1
        print("  %-12s Σ=%9.3f Δ=%9.3f %s" % (name, s, x1 - x0, "OK" if ok else "✗"))
    print("恒等式：%d/%d 通过" % (len(cases) - bad, len(cases)))
    return bad == 0


if __name__ == "__main__":
    import sqlite3
    print("== amv_contrib 自检 ==")
    ok = _selftest()
    # 2026-11-15 公开化：不再写死本机路径；PANEL_V2_WS 优先，否则按本文件位置推导（app/lib → 项目根）。
    W = os.environ.get("PANEL_V2_WS") or os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    adb = os.path.join(W, "data", "amv_board.db")
    if os.path.exists(adb):
        c = sqlite3.connect(adb)
        pdb = os.path.join(W, "data", "ths_board.db")
        c2 = sqlite3.connect(pdb)
        ds = [r[0] for r in c.execute("SELECT date FROM daily_total ORDER BY date DESC LIMIT 2")]
        mem = [r[0] for r in c2.execute(
            "SELECT m.stock FROM board_member m JOIN board b ON b.code=m.board_code "
            "WHERE b.sw_level=2 AND b.code='881166.TI'")]
        r = board_parts(c, mem, ds[-1], ds[0])
        print("\n军工装备(881166) %s→%s：%s" % (ds[-1], ds[0], "无数据" if not r else
              "贡献 %+.3f‰ = " % r["total"] +
              " + ".join("%s %+.3f" % (PART_NAMES[k], r[k]) for k in PART_KEYS if abs(r[k]) >= 0.0005) +
              " + 大盘基准 %+.3f（Σ分量 %.3f‰，成分 %d 只）" % (r["den"], r["cover"], r["n"])))
    sys.exit(0 if ok else 1)
