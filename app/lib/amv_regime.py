# -*- coding: utf-8 -*-
r"""amv_regime.py — AMV 双轨多空判定（口径唯一源）

职责
    把 0AMV 活跃市值（AMV）序列 → 逐日「多/空」确认态（双轨规则）。
    **大盘与板块通用**：同一函数喂不同 AMV 序列即可（大盘=daily_total；板块=sector_daily）。

设计初心（为什么要有这个模块）
    口径唯一源（编程规范 §3）：双轨多空判定只在**此处**定义一次；消费端
    （工作台多空徽标 / 复盘产出）一律 import，禁止各自自建映射——防口径分叉。

口径（逐字照 `夏天-activity/参考卡片/0AMV活跃市值.md` §8.1，主人 2026-09-30 定）
    多空线 = MA10（AMV 的 10 日简单均线）
    进入区间：站上 MA10（AMV>MA10）或 pct≥+4%（空头：跌破 MA10 或 pct≤−2.3%）
    确认：
      ① 双轨共振（同日同时满足）→ 当天确认：
         pct≥+4% 且 AMV>MA10 → 当天确认多头；pct≤−2.3% 且 AMV<MA10 → 当天确认空头
      ② 单轨（只满足一个）→ 等第二个信号：
         第二个 ±%（同轨；默认读法 A=非连续可隔天）或 连续两日站上/跌破 MA10
    阈值：+4% / −2.3%（主人 2026-10-30 定）
    状态机跑全历史；输出逐日确认态 ∈ {多, 空, None}。

上下游
    上游：AMV 序列来源（data/amv_board.db::daily_total 大盘 / ::sector_daily 板块 / 任意序列）
    下游：dashboard/server.py（板块多空徽标）、复盘产出（大盘状态 JSON）
    调用：from amv_regime import compute_regime

只读：本模块不写任何文件；导出 JSON 由 CLI（--export）或调用方决定落点。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ---------------------------------------------------------------- 口径常量（唯一源）
MA_N = 10          # 多空线：AMV 的 10 日简单均线
TH_UP = 0.04       # 多头动量阈值 +4%
TH_DN = -0.023     # 空头动量阈值 −2.3%

# 2026-11-15 公开化：路径不再写死本机。PANEL_V2_WS 优先，否则按本文件位置推导
# （app/lib/amv_regime.py → 上溯三层 = 项目根）。生产环境仍指向 OH-WorkSpace 根。
# 注意：本副本自 2026-11-09 起含「缺失值不参与计算」修复，**已与源分叉**，
#       tools/sync_vendor.py 把它列为冻结项（不再从源覆盖）；合并需人工比对。
WS = os.environ.get("PANEL_V2_WS") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
AMV_DB = os.path.join(WS, 'data', 'amv_board.db')


def sma(vals, n):
    """简单移动均线（前 n−1 个为 None）。"""
    out = [None] * len(vals)
    s = 0.0
    for i, v in enumerate(vals):
        s += v
        if i >= n:
            s -= vals[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def stock_state(vals):
    """个股四态（双线制）判定 —— 口径唯一源（主人 2026-10-01 定，原在 dashboard/server.py::_stock_bull）。

    vals = 个股**活跃市值序列**（按日升序；amv = alive(活跃流通股本) × 不复权收盘，与 api_members2 同口径）。
    白线 = EMA(EMA(amv,10),10)（**非价格白线**）；多空线 = MA10。
      金叉（MA10>白线）：价>MA10 → 多；白线<价<MA10 → 偏多；价<白线 → 空
      死叉（白线>MA10）：价<MA10 → 空；MA10<价<白线 → 偏空；价>白线 → 多
      两线相等 / 缺数（len<12）→ None
    返回 '多'/'偏多'/'空'/'偏空'/None（前端单字展示多/空，夹中间用空心样式区分「偏」）。
    **数据取用（alive/收盘）由调用方负责**，本函数只做判定（纯函数，禁自建第二份）。
    """
    if not vals:
        return None
    # 2026-11-09：与 stock_state_series 同口径——缺失值不参与计算（不当 0、不删位）。
    # 原实现 `sum(vals[-10:])` 遇 None 直接 TypeError；回退到有效值序列后语义与 series 完全一致。
    seq = [v for v in vals if v is not None]
    if len(seq) < 12:
        return None
    ma = sum(seq[-10:]) / 10.0
    e1 = e2 = seq[0]
    a = 2.0 / 11.0
    for v in seq[1:]:
        e1 += a * (v - e1)
        e2 += a * (e1 - e2)
    last = seq[-1]
    if ma > e2:                       # 金叉
        if last > ma:
            return "多"
        if last > e2:
            return "偏多"
        return "空"
    if e2 > ma:                       # 死叉
        if last < ma:
            return "空"
        if last < e2:
            return "偏空"
        return "多"
    return None


def stock_state_series(vals):
    """个股四态**逐日序列**（口径同 stock_state，仅滚动输出，不另立口径）。

    vals = 活跃市值序列（升序）。返回与 vals 等长的 list，元素 ∈ {'多','偏多','空','偏空',None}；
    前 11 个（不足 12 根预热）为 None。判定与 stock_state 逐字一致：
      白线 = EMA(EMA(amv,10),10)；多空线 = MA10；
      金叉（MA10>白线）：价>MA10→多 / 白线<价<MA10→偏多 / 价<白线→空
      死叉（白线>MA10）：价<MA10→空 / MA10<价<白线→偏空 / 价>白线→多
    用途：K 线弹窗「背景带」按日着色（engine/server.py::/api/kline_bg）。stock_state 只给末值，
    此处是它的滚动展开——**判定只在正本定义**，消费端禁自建第二份。
    """
    n = len(vals)
    out = [None] * n
    if n == 0:
        return out
    # 2026-11-09：缺失值语义（口径守门 test_amv_regime_null_semantics）——
    #   缺失日 = 不下判定（保留位置、不删元素、不置零）。
    #   做法：在「有效值序列」上算 EMA / MA10，再按原下标映射回去。
    #   效果：首日缺失 == 从第 1 天开始的序列（种子取首个有效值，不从 0 收敛）；
    #         中途缺失 == 窗口向前顺延到上一个有效值（不是当 0 参与）。
    idx = [i for i, v in enumerate(vals) if v is not None]
    m = len(idx)
    if m == 0:
        return out
    seq = [vals[i] for i in idx]
    a = 2.0 / 11.0
    e1 = e2 = seq[0]
    ema2 = [0.0] * m
    ema2[0] = seq[0]
    for k in range(1, m):
        e1 += a * (seq[k] - e1)
        e2 += a * (e1 - e2)
        ema2[k] = e2
    s = 0.0
    for k in range(m):
        s += seq[k]
        if k >= 10:
            s -= seq[k - 10]
        if k < 11:
            continue
        ma = s / 10.0
        last = seq[k]
        ev = ema2[k]
        if ma > ev:                   # 金叉
            out[idx[k]] = "多" if last > ma else ("偏多" if last > ev else "空")
        elif ev > ma:                 # 死叉
            out[idx[k]] = "空" if last < ma else ("偏空" if last < ev else "多")
    return out


def compute_regime_single(dates, amv, ma_n=MA_N):
    """单轨 MA10 变体（测试用）：AMV>MA10→多，<→空；切换日 = 上/下穿 MA10 当日（无额外确认）。

    与 compute_regime（双轨二态）区分：本函数不做 ±4%/−2.3% 动量轨与共振/二次确认，
    仅供本回测（板块择时）指定变体；**非现行口径**，默认调用方仍走 compute_regime（mode='dual'）。
    """
    n = len(amv)
    ma = sma(amv, ma_n)
    pct = [None] * n
    for i in range(1, n):
        if amv[i - 1]:
            pct[i] = amv[i] / amv[i - 1] - 1.0
    state = [None] * n
    switches = []
    C = None
    for i in range(n):
        if ma[i] is not None:
            s = '多' if amv[i] > ma[i] else ('空' if amv[i] < ma[i] else C)
            if s is not None and s != C:
                switches.append({'i': i, 'date': dates[i], 'dir': s, 'mark': '单轨', 'trigger': 'P',
                                 'delay_any': 0, 'delay_track': 0})
                C = s
        state[i] = C
    return {'state': state, 'ma': ma, 'pct': pct, 'mark': [None] * n,
            'trigger': [None] * n, 'switches': switches}


def compute_regime(dates, amv, ma_n=MA_N, th_up=TH_UP, th_dn=TH_DN, reading="A", mode="dual"):
    """核心：AMV 序列 → 逐日双轨确认态（mode='dual'，默认）或单轨（mode='single'）。

    mode='single' → 转 compute_regime_single（AMV>MA10→多 / <→空，切换日=穿线当日）；默认 'dual' 行为不变。

    参数
        dates: 交易日序列（升序，'YYYY-MM-DD'）
        amv  : 与 dates 等长的 AMV 数值序列
        ma_n : 多空线均线周期（默认 10）
        th_up/th_dn: 动量阈值（默认 +4% / −2.3%）
        reading: 「第二个信号」读法——'A' 非连续（同轨信号再次出现即算，默认）｜'B' 连续两日
        mode   : 'dual'（默认，双轨二态）｜'single'（单轨 MA10 变体，测试用）

    返回 dict
        state[i]   : 第 i 日确认态 ∈ {'多','空',None}（None=尚未确认，如预热期）
        ma[i]      : 多空线值（前 ma_n−1 个 None）
        pct[i]     : 当日 AMV 环比（第 0 个 None）
        mark[i]    : 该日若为切换确认日，标 '共振' / '二次A' / '二次B'，否则 None
        trigger[i] : 切换触发轨 'P'(价格轨) / 'M'(动量轨) / 'P+M'(共振)
        switches   : [{i,date,dir,mark,trigger,delay_any,delay_track}]
    """
    if mode == "single":
        return compute_regime_single(dates, amv, ma_n)
    n = len(amv)
    ma = sma(amv, ma_n)
    pct = [None] * n
    for i in range(1, n):
        if amv[i - 1]:
            pct[i] = amv[i] / amv[i - 1] - 1.0

    # 两轨信号（逐日）
    Pb = [False] * n    # 价格轨-多：AMV>MA10
    Pbr = [False] * n   # 价格轨-空：AMV<MA10
    Mb = [False] * n    # 动量轨-多：pct≥+4%
    Mbr = [False] * n   # 动量轨-空：pct≤−2.3%
    for i in range(n):
        if ma[i] is not None:
            Pb[i] = amv[i] > ma[i]
            Pbr[i] = amv[i] < ma[i]
        if pct[i] is not None:
            Mb[i] = pct[i] >= th_up
            Mbr[i] = pct[i] <= th_dn

    state = [None] * n
    mark = [None] * n
    trig = [None] * n
    switches = []
    C = None
    first_idx = {"多": None, "空": None}   # 该方向自上次切换以来首个触发日（任意轨）
    firstP = {"多": None, "空": None}      # 价格轨首触发日
    firstM = {"多": None, "空": None}      # 动量轨首触发日
    seen = {"多": {"P": False, "M": False}, "空": {"P": False, "M": False}}

    for i in range(n):
        pb, pbr, mb, mbr = Pb[i], Pbr[i], Mb[i], Mbr[i]
        cd = mk = tr = None
        # ① 双轨共振（同日同向同时满足）→ 当天确认
        if mb and pb and C != "多":
            cd, mk, tr = "多", "共振", "P+M"
        elif mbr and pbr and C != "空":
            cd, mk, tr = "空", "共振", "P+M"
        else:
            # ② 单轨 → 等第二个信号（同轨）
            cands = []
            for d, Pd, Md in (("多", pb, mb), ("空", pbr, mbr)):
                if d == C:
                    continue
                if reading == "B":
                    Pprev = (Pb[i - 1] if d == "多" else Pbr[i - 1]) if i > 0 else False
                    Mprev = (Mb[i - 1] if d == "多" else Mbr[i - 1]) if i > 0 else False
                    okP, okM = (Pd and Pprev), (Md and Mprev)
                else:  # 'A' 非连续
                    okP, okM = (Pd and seen[d]["P"]), (Md and seen[d]["M"])
                if okP or okM:
                    cands.append((d, ("P" if okP else "") + ("M" if okM else "")))
            if len(cands) == 1:
                cd, tr = cands[0]
                mk = "二次" + reading
            elif len(cands) > 1:
                # 罕见冲突：同日多/空均可确认 → 以价格轨指向优先
                cd, tr = ("多", "P") if pb else (("空", "P") if pbr else cands[0])
                mk = "二次" + reading

        if cd is not None and cd != C:
            fa = first_idx[cd]
            if fa is None:
                fa = i
            cand = []
            if "P" in (tr or ""):
                cand.append(firstP[cd])
            if "M" in (tr or ""):
                cand.append(firstM[cd])
            cand = [c for c in cand if c is not None]
            ft = min(cand) if cand else i
            switches.append({"i": i, "date": dates[i], "dir": cd, "mark": mk, "trigger": tr,
                             "delay_any": i - fa, "delay_track": i - ft})
            C = cd
            first_idx = {"多": None, "空": None}
            firstP = {"多": None, "空": None}
            firstM = {"多": None, "空": None}
            seen = {"多": {"P": False, "M": False}, "空": {"P": False, "M": False}}

        state[i] = C
        mark[i] = mk
        trig[i] = tr
        for d, Pd, Md in (("多", pb, mb), ("空", pbr, mbr)):
            if (Pd or Md) and first_idx[d] is None:
                first_idx[d] = i
            if Pd:
                seen[d]["P"] = True
                if firstP[d] is None:
                    firstP[d] = i
            if Md:
                seen[d]["M"] = True
                if firstM[d] is None:
                    firstM[d] = i

    return {"state": state, "ma": ma, "pct": pct, "mark": mark, "trigger": trig, "switches": switches}


def latest_state(dates, amv, **kw):
    """便捷：返回最后一个交易日的确认态信息（含是否共振确认）。"""
    r = compute_regime(dates, amv, **kw)
    i = len(amv) - 1
    sw = r["switches"][-1] if r["switches"] else None
    return {
        "date": dates[i], "state": r["state"][i], "ma": r["ma"][i], "pct": r["pct"][i],
        "amv": amv[i], "mark": r["mark"][i], "trigger": r["trigger"][i],
        "last_switch": sw,
        "resonance": bool(r["mark"][i] == "共振"),
    }


# ---------------------------------------------------------------- 数据加载（只读）
def _load_total(dates=None):
    import sqlite3
    con = sqlite3.connect(AMV_DB)
    try:
        rows = con.execute("SELECT date, total FROM daily_total ORDER BY date").fetchall()
    finally:
        con.close()
    d = [r[0] for r in rows]
    a = [float(r[1]) / 1e8 for r in rows]   # 元 → 亿元
    return d, a


def _load_sector(code, level=None):
    import sqlite3
    con = sqlite3.connect(AMV_DB)
    try:
        if level:
            rows = con.execute("SELECT date, amv FROM sector_daily WHERE code=? AND level=? ORDER BY date",
                               (code, level)).fetchall()
        else:
            rows = con.execute("SELECT date, amv FROM sector_daily WHERE code=? ORDER BY date", (code,)).fetchall()
    finally:
        con.close()
    return [r[0] for r in rows], [float(r[1]) for r in rows]


def market_regime_payload(asof=None):
    """大盘双轨状态载荷（供复盘读取）。字段：date/state/resonance/ma10/pct/amv/近端切换。"""
    d, a = _load_total()
    if asof:
        keep = [i for i, x in enumerate(d) if x <= asof]
        d = [d[i] for i in keep]
        a = [a[i] for i in keep]
    r = compute_regime(d, a)
    i = len(d) - 1
    sw = r["switches"][-1] if r["switches"] else None
    recent = [s for s in r["switches"][-6:]]
    return {
        "date": d[i],
        "state": r["state"][i],                       # '多' / '空'
        "resonance": bool(r["mark"][i] == "共振"),     # 当日是否共振确认
        "ma10": None if r["ma"][i] is None else round(r["ma"][i], 2),
        "pct": None if r["pct"][i] is None else round(r["pct"][i] * 100, 2),
        "amv": round(a[i], 2),
        "last_switch": sw,
        "recent_switches": recent,
        "caliber": "AMV 双轨多空判定（MA10 + pct±4%/−2.3%；共振当天/单轨第二个）· 参考卡片 §8.1",
        "source": "data/amv_board.db::daily_total",
        "generated_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _main():
    ap = argparse.ArgumentParser(description="AMV 双轨多空判定（口径唯一源）")
    ap.add_argument("--source", default="total", choices=["total", "sector"], help="数据源：total=大盘 / sector=板块")
    ap.add_argument("--code", default="", help="--source sector 时的板块代码（如 BK1462）")
    ap.add_argument("--level", default="", help="--source sector 时的层级（L1/L2/L3/CONCEPT/INDEX）")
    ap.add_argument("--asof", default="", help="截至日 YYYY-MM-DD（默认最新）")
    ap.add_argument("--reading", default="A", choices=["A", "B"])
    ap.add_argument("--export", default="", help="导出大盘状态 JSON 到该路径")
    args = ap.parse_args()

    if args.source == "total":
        payload = market_regime_payload(args.asof or None)
        if args.export:
            os.makedirs(os.path.dirname(os.path.abspath(args.export)), exist_ok=True)
            with open(args.export, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=1)
            print("已导出:", args.export)
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        if not args.code:
            print("--source sector 需 --code", file=sys.stderr)
            sys.exit(2)
        d, a = _load_sector(args.code, args.level or None)
        r = compute_regime(d, a, reading=args.reading)
        i = len(d) - 1
        print(json.dumps({"code": args.code, "date": d[i], "state": r["state"][i],
                          "ma": None if r["ma"][i] is None else round(r["ma"][i], 2),
                          "pct": None if r["pct"][i] is None else round(r["pct"][i] * 100, 2),
                          "last_switch": r["switches"][-1] if r["switches"] else None},
                         ensure_ascii=False, indent=1))


if __name__ == "__main__":
    _main()
