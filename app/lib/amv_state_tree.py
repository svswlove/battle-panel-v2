# -*- coding: utf-8 -*-
r"""amv_state_tree.py — AMV 多空判定状态树（口径唯一源 · 纯函数）

职责
    把 AMV 日线序列 + 全A（880008）日 K → 「多头启动 / 空头启动」两条状态树，逐日可回放。
    判定逻辑全在本文件；消费端（8002 盘中页、复盘、单测）一律 import，禁止自建第二份。

口径（主人 2026-10-30 定，任务书 `任务书/20261030_多空判定状态树_任务书.md` §1）
    多头启动 —— 触发（任一）
        单日 pct ≥ +4%
        两日累计 ≥ +6%
        三日累计 ≥ +6.5%
    多头启动 —— 附加条件（必须同时满足，否则不点亮）
        形态：880008 当日 K，下影线「明显」→ 否决
              明显 = 下影 ≥ 实体 × 20% **且** 下影 ≥ 收盘 × 0.3%（两条都满足才算明显）
              下影 = min(开, 收) − 最低；实体 = |收 − 开|
        时限：触发后 T+1、T+2 两个交易日内**收盘**须站上 AMV 的 MA10；
              T+2 收盘仍未站上 → 作废（灰，不再点亮）
    空头启动 —— 并列，任一触发
        单日 pct ≤ −2.3%（阈值唯一源 amv_regime.TH_DN）
        AMV < MA10
        MACD 死叉（AMV 12/26/9）
        W3 信号 —— 口径未定，本期占位不实现（节点恒 '占位'，不参与判定）

节点三态（盘中页展示用）
    '未达成'   条件未满足
    '盘中达成' 盘中实时满足，收盘未定（允许回退）
    '收盘确认' 收盘后确认满足
    另两档：'否决'（形态明显）/ '作废'（时限到未站上）/ '缺数'（K 线缺该日）

上下游
    上游：amv_regime（AMV 日线 + MA10 + 阈值唯一源）、eltdx sh880008 日 K（形态）、
          data/panel/amv_live_sector_track.json（今日分钟序列，盘中进度）
    下游：`projects/panel-v2/app/plugins/state_tree/backend.py`（8810 `/api/p/state_tree/*`）。
      旧 8002 `dashboard/server.py::api_state_tree` 已于 2026-10-30 下线并销毁。
      注：旧 8002 直接调本文件的 `build_payload/load_daily/load_bars`（自带 sqlite/eltdx）；
      8810 侧只经 `ctx.lib` 拿**纯函数**，取数走 `ctx.db_read` / `ctx.quotes` / `ctx.amv_live`。

只读：本模块不写任何文件。
"""
from __future__ import annotations

import datetime as _dt
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import amv_regime as _regime  # noqa: E402  口径唯一源（MA10 / 阈值 / AMV 日线）

# ---------------------------------------------------------------- 口径常量（唯一源）
TH_UP_1D = _regime.TH_UP          # 多头单日 +4%
TH_UP_2D = 0.06                   # 多头两日累计 +6%
TH_UP_3D = 0.065                  # 多头三日累计 +6.5%
TH_DN = _regime.TH_DN             # 空头单日 −2.3%（跟随 amv_regime）
MA_N = _regime.MA_N               # 多空线 MA10
SHADOW_BODY_RATIO = 0.20          # 下影 ≥ 实体 × 20%
SHADOW_CLOSE_RATIO = 0.003        # 下影 ≥ 收盘 × 0.3%
DEADLINE_T = 2                    # 触发后 T+1/T+2 两个交易日内须站上 MA10
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9

# 节点态
S_MISS = "未达成"
S_LIVE = "盘中达成"
S_CLOSE = "收盘确认"
S_VETO = "否决"
S_VOID = "作废"
S_NA = "缺数"
S_HOLD = "占位"                   # W3 未定义

# 当日结果
R_IDLE = "未启动"
R_PENDING = "待确认"
R_NO_LIGHT = "不点亮"
R_LIT = "点亮"
R_VOID = "作废"


# ---------------------------------------------------------------- 形态判定（纯函数）
def pattern_verdict(bar):
    """880008 单日 K → 下影线「明显」判定。

    bar: {'open','high','low','close'}（数字）。缺任一 → {'state': '缺数'}。

    「明显」= 下影 ≥ 实体 × 20% **且** 下影 ≥ 收盘 × 0.3%，两条都满足 → 否决（ok=False）。
    实体为 0（开=收）时：仍有下影 → 占实体比视为无穷（满足第一条）；下影为 0 → 不满足。
    """
    if not isinstance(bar, dict):
        return {"state": S_NA, "ok": None, "reason": "无当日 K 线"}
    try:
        o = float(bar["open"])
        c = float(bar["close"])
        lo = float(bar["low"])
    except (KeyError, TypeError, ValueError):
        return {"state": S_NA, "ok": None, "reason": "K 线缺 open/close/low"}
    body = abs(c - o)
    lower = min(o, c) - lo
    if lower < 0:                 # 脏 K（最低价高于开收）→ 按 0 处理，不静默编造
        lower = 0.0
    if body > 0:
        ratio_body = lower / body
        body_ok = ratio_body >= SHADOW_BODY_RATIO
    else:
        ratio_body = None         # 无实体：不参与比例判据
        body_ok = lower > 0
    ratio_close = (lower / c) if c else None
    close_ok = ratio_close is not None and ratio_close >= SHADOW_CLOSE_RATIO
    obvious = bool(body_ok and close_ok)
    return {
        "state": S_VETO if obvious else S_CLOSE,
        "ok": not obvious,
        "open": round(o, 2), "close": round(c, 2),
        "high": round(float(bar.get("high", c)), 2), "low": round(lo, 2),
        "body": round(body, 2),
        "lower_shadow": round(lower, 3),
        "ratio_body": None if ratio_body is None else round(ratio_body * 100, 1),
        "ratio_close": None if ratio_close is None else round(ratio_close * 100, 3),
        "th_body": round(body * SHADOW_BODY_RATIO, 3),
        "th_close": round(c * SHADOW_CLOSE_RATIO, 3),
        "reason": ("下影 %.2f ≥ 实体×20%%(%.2f) 且 ≥ 收盘×0.3%%(%.2f) → 明显 → 否决"
                   % (lower, body * SHADOW_BODY_RATIO, c * SHADOW_CLOSE_RATIO)) if obvious
                  else ("下影 %.2f 未同时满足「≥实体×20%%(%.2f)」「≥收盘×0.3%%(%.2f)」→ 不明显 → 合格"
                        % (lower, body * SHADOW_BODY_RATIO, c * SHADOW_CLOSE_RATIO)),
    }


# ---------------------------------------------------------------- MACD（纯函数）
def _ema(vals, n):
    if not vals:
        return []
    a = 2.0 / (n + 1.0)
    e = float(vals[0])
    out = [e]
    for v in vals[1:]:
        e += a * (float(v) - e)
        out.append(e)
    return out


def macd_series(vals, fast=MACD_FAST, slow=MACD_SLOW, signal=MACD_SIGNAL):
    """AMV 序列 → (dif, dea)；长度与 vals 相同（前段为预热值，不做截断）。"""
    if not vals:
        return [], []
    e1 = _ema(vals, fast)
    e2 = _ema(vals, slow)
    dif = [a - b for a, b in zip(e1, e2)]
    dea = _ema(dif, signal)
    return dif, dea


def macd_dead_cross(vals, fast=MACD_FAST, slow=MACD_SLOW, signal=MACD_SIGNAL):
    """逐日 MACD 死叉标记：DIF 由 ≥DEA 转为 <DEA 的那一天为 True。"""
    dif, dea = macd_series(vals, fast, slow, signal)
    out = [False] * len(dif)
    for i in range(1, len(dif)):
        if dif[i - 1] >= dea[i - 1] and dif[i] < dea[i]:
            out[i] = True
    return out


# ---------------------------------------------------------------- 触发判定（纯函数）
def trigger_flags(dates, amv):
    """逐日多头触发标记。→ [{d1,d2,d3,met,kind,value}]

    d1 = 单日 pct ≥ +4%；d2 = 两日累计 ≥ +6%；d3 = 三日累计 ≥ +6.5%。
    kind 按 单日 → 两日 → 三日 顺序取首个命中（同日多命中时以最强触发记）。
    """
    n = len(amv)
    out = []
    for i in range(n):
        p1 = (amv[i] / amv[i - 1] - 1.0) if i >= 1 and amv[i - 1] else None
        p2 = (amv[i] / amv[i - 2] - 1.0) if i >= 2 and amv[i - 2] else None
        p3 = (amv[i] / amv[i - 3] - 1.0) if i >= 3 and amv[i - 3] else None
        d1 = p1 is not None and p1 >= TH_UP_1D
        d2 = p2 is not None and p2 >= TH_UP_2D
        d3 = p3 is not None and p3 >= TH_UP_3D
        kind = "单日+4%" if d1 else ("两日+6%" if d2 else ("三日+6.5%" if d3 else None))
        out.append({"d1": bool(d1), "d2": bool(d2), "d3": bool(d3),
                    "met": bool(kind), "kind": kind,
                    "p1": None if p1 is None else round(p1 * 100, 2),
                    "p2": None if p2 is None else round(p2 * 100, 2),
                    "p3": None if p3 is None else round(p3 * 100, 2)})
    return out


# ---------------------------------------------------------------- 状态树（纯函数 · 核心）
def evaluate(dates, amv, bars=None, *, live=False, today=None):
    """AMV 日线 + 880008 日 K → 多空状态树（逐日回放 + 当日视图）。

    参数
        dates: 交易日 'YYYY-MM-DD' 升序
        amv  : 与 dates 等长的 AMV 序列（亿元）
        bars : {YYYYMMDD 或 YYYY-MM-DD: {open,high,low,close}}，缺该日 → 形态节点 '缺数'
        live : True = 最后一根是盘中实时值（未收盘）→ 节点态落 '盘中达成'，不落 '收盘确认'
        today: 覆盖「今天」的日期串（默认取 dates[-1]），供回放测试

    返回
        {'days': [...], 'bull': {...}, 'bear': {...}, 'ma': [...], 'pct': [...]}
        days[i] = {date, amv, ma10, pct, trigger, pattern, deadline, result}
    """
    n = len(amv)
    bars = bars or {}
    norm = {}
    for k, v in bars.items():
        norm[str(k).replace("-", "")] = v

    ma = _regime.sma(amv, MA_N)
    pct = [None] * n
    for i in range(1, n):
        if amv[i - 1]:
            pct[i] = amv[i] / amv[i - 1] - 1.0
    trig = trigger_flags(dates, amv)
    dead = macd_dead_cross(amv)

    def _key(d):
        return str(d).replace("-", "")

    days = []
    window = None            # 当前启动窗口
    for i in range(n):
        is_live = bool(live and i == n - 1)
        d8 = _key(dates[i])
        pat = pattern_verdict(norm.get(d8))
        if pat["ok"] is True and is_live and pat["state"] == S_CLOSE:
            pat["state"] = S_LIVE

        # ① 已有窗口 → 先推进时限节点（触发日当天不检查，T+1 起）
        if window is not None and window["status"] == "open":
            j = i - window["i"]
            if 1 <= j <= DEADLINE_T:
                stand = ma[i] is not None and amv[i] > ma[i]
                if stand:
                    window["status"] = "confirmed"
                    window["hit"] = {"date": dates[i], "t": "T+%d" % j}
                elif j == DEADLINE_T:
                    window["status"] = "void"
                    window["void_at"] = {"date": dates[i], "t": "T+%d" % j}
                window["checks"].append({
                    "date": dates[i], "t": "T+%d" % j, "amv": round(amv[i], 2),
                    "ma10": None if ma[i] is None else round(ma[i], 2), "stand": bool(stand),
                })

        # ② 当日触发 → 开窗。窗口未结（含触发当日、T+1/T+2 内）时不再重开：
        #    同一次启动内的后续触发属补充信号，不另起一个窗口（否则确认当日会被新窗口盖掉）。
        if trig[i]["met"] and window is None:
            window = {"i": i, "date": dates[i], "kind": trig[i]["kind"],
                      "status": "open", "checks": [], "hit": None, "void_at": None,
                      "pattern_ok": pat["ok"]}

        # ③ 当日视图
        dl = _deadline_node(window, i, is_live, ma, amv, dates)
        result = _day_result(trig[i], pat, dl, window, i, is_live)
        days.append({
            "date": dates[i], "amv": round(amv[i], 2),
            "ma10": None if ma[i] is None else round(ma[i], 2),
            "pct": None if pct[i] is None else round(pct[i] * 100, 2),
            "trigger": dict(trig[i], state=(S_MISS if not trig[i]["met"] else (S_LIVE if is_live else S_CLOSE))),
            "pattern": pat,
            "deadline": dl,
            "result": result,
        })
        # ④ 窗口已结（点亮/作废）→ 收窗；之后的日子回到「未启动」，不重复计作废
        if window is not None and window["status"] != "open" and i >= window["i"]:
            window = None

    last_live = bool(live)
    bull = _bull_view(days[-1], trig[-1], window, n, last_live)
    bear = _bear_view(dates, amv, ma, pct, dead, last_live)
    return {"days": days, "bull": bull, "bear": bear,
            "ma": ma, "pct": pct}


def _deadline_node(window, i, is_live, ma, amv, dates):
    """时限节点：窗口内 T+1/T+2 收盘站上 MA10 的进度。"""
    if window is None:
        return {"state": S_MISS, "label": "T+1/T+2 收盘站上 MA10", "window": None,
                "checks": [], "reason": "无启动窗口"}
    j = i - window["i"]
    checks = list(window["checks"])
    base = {"label": "T+1/T+2 收盘站上 MA10",
            "window": {"trigger_date": window["date"], "trigger_kind": window["kind"],
                       "status": window["status"], "hit": window["hit"], "void_at": window["void_at"]},
            "checks": checks}
    if window["status"] == "confirmed":
        return dict(base, state=(S_LIVE if is_live else S_CLOSE),
                    reason="%s 收盘 %.2f > MA10 %.2f → 确认"
                           % (window["hit"]["date"], amv[_idx(dates, window["hit"]["date"])],
                              ma[_idx(dates, window["hit"]["date"])]))
    if window["status"] == "void":
        return dict(base, state=S_VOID,
                    reason="T+2（%s）收盘仍未站上 MA10 → 作废" % window["void_at"]["date"])
    if j <= 0:
        return dict(base, state=S_MISS, reason="触发日当天，T+1/T+2 待验证")
    # 窗口内、未到 T+2：盘中值满足 → 盘中达成
    if is_live and ma[i] is not None and amv[i] > ma[i]:
        return dict(base, state=S_LIVE, reason="盘中已站上 MA10，收盘待确认")
    return dict(base, state=S_MISS,
                reason="T+%d 未站上 MA10（还差 %d 个交易日）" % (j, DEADLINE_T - j))


def _idx(dates, date):
    for k, d in enumerate(dates):
        if d == date:
            return k
    return len(dates) - 1


def _day_result(trig, pat, dl, window, i, is_live):
    """当日结果：作废 > 不点亮(形态否决) > 点亮 > 待确认 > 未启动。

    形态判据取**开窗那天**（window['pattern_ok']），不取当日：
    窗口内非触发日的 K 线与本次启动无关，拿它的下影去否决本次启动是错的。
    """
    if dl["state"] == S_VOID:
        return R_VOID
    if window is None:
        return R_IDLE
    if window.get("pattern_ok") is False:
        return R_NO_LIGHT
    if dl["state"] in (S_CLOSE, S_LIVE):
        return R_LIT
    if is_live and trig["met"]:
        return R_PENDING
    return R_PENDING if window["status"] == "open" else R_IDLE


def _bull_view(day, trig, window, n, is_live):
    """当日多头状态树视图（三个条件节点 + 结果）。"""
    return {
        "result": day["result"],
        "trigger": {
            "state": day["trigger"]["state"],
            "kind": day["trigger"]["kind"],
            "nodes": [
                {"key": "d1", "label": "单日 ≥ +4%", "met": trig["d1"],
                 "value": trig["p1"], "th": round(TH_UP_1D * 100, 2)},
                {"key": "d2", "label": "两日累计 ≥ +6%", "met": trig["d2"],
                 "value": trig["p2"], "th": round(TH_UP_2D * 100, 2)},
                {"key": "d3", "label": "三日累计 ≥ +6.5%", "met": trig["d3"],
                 "value": trig["p3"], "th": round(TH_UP_3D * 100, 2)},
            ],
        },
        "pattern": day["pattern"],
        "deadline": day["deadline"],
    }


def _bear_view(dates, amv, ma, pct, dead, is_live):
    """当日空头状态树视图（四个并列条件，任一触发即点亮）。"""
    i = len(amv) - 1
    close_state = S_LIVE if is_live else S_CLOSE
    dn_pct = None if pct[i] is None else pct[i] * 100
    nodes = [
        {"key": "pct_dn", "label": "单日 ≤ −2.3%",
         "met": bool(dn_pct is not None and pct[i] <= TH_DN),
         "value": None if dn_pct is None else round(dn_pct, 2),
         "th": round(TH_DN * 100, 2)},
        {"key": "below_ma10", "label": "AMV < MA10",
         "met": bool(ma[i] is not None and amv[i] < ma[i]),
         "value": round(amv[i], 2),
         "th": None if ma[i] is None else round(ma[i], 2)},
        {"key": "macd_dead", "label": "MACD 死叉", "met": bool(dead[i]), "value": None, "th": None},
        {"key": "w3", "label": "W3 信号（口径待定）", "met": None, "value": None, "th": None,
         "placeholder": True},
    ]
    for nd in nodes:
        if nd.get("placeholder"):
            nd["state"] = S_HOLD
        else:
            nd["state"] = close_state if nd["met"] else S_MISS
    return {"result": R_LIT if any(x["met"] for x in nodes if not x.get("placeholder")) else R_IDLE,
            "nodes": nodes}


# ---------------------------------------------------------------- 分钟序列清洗（纯函数）
SESSION_MINUTES = (9 * 60 + 30, 11 * 60 + 30, 13 * 60, 15 * 60)


def _minute_grid(date8):
    """当日分钟格（09:30–11:30 / 13:00–15:00，含端点）→ ['HH:MM', ...]。"""
    out = []
    for lo, hi in ((SESSION_MINUTES[0], SESSION_MINUTES[1]), (SESSION_MINUTES[2], SESSION_MINUTES[3])):
        for m in range(lo, hi + 1):
            out.append("%02d:%02d" % (m // 60, m % 60))
    return out


def clean_minute_points(points, *, jump_pp=1.0, gap_min=3, date8=None):
    """分钟序列脏点过滤 + 缺口补中位数（口径见任务书 §4）。

    points: [{'ts': 'YYYY-MM-DD HH:MM:SS', 'all': -2.19}, ...]（可乱序）
    规则① 单条 all 与**保留序列**的前一条偏离 > jump_pp（百分点）→ 丢弃
    规则② 丢弃后连续缺 ≥ gap_min 条时，缺口中每个分钟取「相邻两个已保留点的中位数」
    → (kept, stats)；补出来的点带 filled=True。
    """
    seq = []
    for p in points or []:
        ts = str(p.get("ts") or "")
        v = p.get("all")
        if len(ts) < 16 or v is None:
            continue
        try:
            seq.append((ts, float(v)))
        except (TypeError, ValueError):
            continue
    seq.sort(key=lambda x: x[0])

    kept, dropped = [], []
    for ts, v in seq:
        if kept and abs(v - kept[-1][1]) > jump_pp:
            dropped.append({"ts": ts, "all": round(v, 4), "prev": round(kept[-1][1], 4)})
            continue
        kept.append((ts, v))

    filled_pts = []
    if date8 and len(kept) >= 2:
        d8 = str(date8).replace("-", "")
        prefix = "%s-%s-%s " % (d8[:4], d8[4:6], d8[6:])
        have = {ts[11:16]: v for ts, v in kept}
        prev_hm, run = None, []
        for hm in _minute_grid(d8):
            if hm in have:
                if len(run) >= gap_min and prev_hm is not None:
                    med = round((have[prev_hm] + have[hm]) / 2.0, 4)
                    for m in run:
                        filled_pts.append((prefix + m + ":00", med))
                run, prev_hm = [], hm
            else:
                run.append(hm)

    merged = [(ts, v, False) for ts, v in kept] + [(ts, v, True) for ts, v in filled_pts]
    merged.sort(key=lambda x: x[0])
    out = []
    for ts, v, f in merged:
        item = {"ts": ts, "all": round(v, 4)}
        if f:
            item["filled"] = True
        out.append(item)
    return out, {"n_in": len(seq), "n_kept": len(out), "n_dropped": len(dropped),
                 "n_filled": len(filled_pts), "dropped": dropped}


# ---------------------------------------------------------------- 数据装配（I/O · 供 API 调用）
PANEL_DIR = os.path.join(_regime.WS, "data", "panel")
TRACK_FILE = os.path.join(PANEL_DIR, "amv_live_sector_track.json")
ALL_A_FILE = os.path.join(PANEL_DIR, "all_a_latest.json")


def load_daily(asof=None):
    """AMV 日线（口径唯一源 amv_regime._load_total）→ (dates, amv)。"""
    dates, amv = _regime._load_total()
    if asof:
        keep = [i for i, d in enumerate(dates) if d <= asof]
        dates = [dates[i] for i in keep]
        amv = [amv[i] for i in keep]
    return dates, amv


def load_bars(count=90):
    """880008 日 K（eltdx 现成链路，与 fupan_all_a.py 同源同码）→ {YYYYMMDD: {...}}。

    取数失败返回 ({}, 错误串)；不静默造数。
    """
    sp = os.path.join(_regime.WS, "envs", "qmt312", "Lib", "site-packages")
    try:
        if sp not in sys.path:
            sys.path.insert(0, sp)
        from eltdx import TdxClient  # noqa: PLC0415
        with TdxClient(timeout=10, probe_hosts=False) as cli:
            r = cli.bars.get("sh880008", period="day", count=count)
        out = {}
        for b in getattr(r, "bars", None) or []:
            out[b.time.strftime("%Y%m%d")] = {
                "open": round(b.open, 2), "high": round(b.high, 2),
                "low": round(b.low, 2), "close": round(b.close, 2)}
        if not out:
            return {}, "eltdx sh880008 返回空"
        return out, ""
    except Exception as e:  # noqa: BLE001 - 取数失败如实上报，不阻断状态树其它节点
        return {}, "%s: %s" % (type(e).__name__, e)


def load_today_live(date8):
    """今日分钟 AMV 序列（amv_live_sector_track.json）+ 清洗 → (points, stats)。"""
    import json  # noqa: PLC0415
    try:
        with open(TRACK_FILE, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception as e:  # noqa: BLE001
        return [], {"error": "%s: %s" % (type(e).__name__, e)}
    raw = []
    for k, snaps in (doc.get("dates") or {}).items():
        if str(k).replace("-", "") != str(date8).replace("-", ""):
            continue
        raw = list(snaps or [])
        break
    return clean_minute_points(raw, date8=str(date8).replace("-", ""))


def _envelope(points, bars, today8):
    """诊断：分钟 `all` 是否超出「全A当日振幅包络 ±1.0pp」。

    为什么加这个：任务书 §4 的脏点规则是「与前一分钟偏离 > 1.0pp 则丢弃」，
    但 2026-10-30 实测最大相邻跳变只有 0.918pp（13:00:48），规则命不中已知脏区
    （13:01–13:07 是个 7 分钟的台阶，不是单点尖尖）。任务书自己的旁证是
    「同期上证涨幅不足 1%」→ 包络才是对得上的参照。
    本函数**只诊断不丢点**（口径未定，不自行改判定规则）：把越界点如实列给主人。
    """
    out = {"hi_pct": None, "lo_pct": None, "breaches": []}
    keys = sorted(bars or {})
    if today8 not in keys or keys.index(today8) == 0:
        return out
    k = keys.index(today8)
    prev_c = bars[keys[k - 1]].get("close")
    bar = bars[today8]
    if not prev_c:
        return out
    hi = (bar["high"] / prev_c - 1.0) * 100.0
    lo = (bar["low"] / prev_c - 1.0) * 100.0
    out["hi_pct"] = round(hi, 2)
    out["lo_pct"] = round(lo, 2)
    out["breaches"] = [p["ts"] for p in points
                       if p["all"] > hi + 1.0 or p["all"] < lo - 1.0]
    return out


def build_payload(asof=None, live_pct=None, *, live_mode=False, bars=None, dates=None, amv=None):
    """装配 8002 /api/state_tree 载荷。

    live_pct：盘中实时「全A活跃涨跌%」（来自 8800 live 转发或 track 末点）——
              末根 AMV = 昨收 AMV × (1 + live_pct/100)，节点态落 '盘中达成'。
    live_mode=True 时把末根当「盘中未收盘」。
    """
    if dates is None or amv is None:
        dates, amv = load_daily(asof)
    if bars is None:
        bars, bars_err = load_bars(90)
    else:
        bars_err = ""
    dates = list(dates)
    amv = list(amv)

    today8 = str(dates[-1]).replace("-", "")
    live_used = False
    if live_pct is not None and len(amv) >= 2 and amv[-2]:
        # 盘中实时值递推末根（口径同 amv_live：AMV = 昨收 AMV × (1+涨跌%)，只是未收盘）
        amv[-1] = amv[-2] * (1.0 + float(live_pct) / 100.0)
        live_used = True

    tree = evaluate(dates, amv, bars, live=bool(live_mode or live_used))
    points, stats = load_today_live(today8)
    stats["envelope"] = _envelope(points, bars, today8)
    last = tree["days"][-1]

    return {
        "ok": True,
        "asof": dates[-1],
        "phase": "live" if (live_mode or live_used) else "close",
        "amv": round(amv[-1], 2),
        "ma10": last["ma10"],
        "pct": round((amv[-1] / amv[-2] - 1) * 100, 2) if len(amv) > 1 and amv[-2] else None,
        "bull": tree["bull"],
        "bear": tree["bear"],
        "days": tree["days"][-60:],
        "intraday": {"date": today8, "points": points, "stats": stats},
        "caliber": ("多头触发 单日+4%/两日+6%/三日+6.5% ∧ 形态(880008 下影不明显) ∧ "
                    "T+1/T+2 收盘站上 MA10；空头 单日≤−2.3% / AMV<MA10 / MACD死叉 / W3(待定)"),
        "source": {"amv": "data/amv_board.db::daily_total（经 amv_regime）",
                   "bars": "eltdx:sh880008 日K" if not bars_err else ("缺数：" + bars_err),
                   "intraday": "data/panel/amv_live_sector_track.json"},
        "generated_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(build_payload(), ensure_ascii=False, indent=1)[:4000])
