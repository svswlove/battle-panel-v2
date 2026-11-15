# -*- coding: utf-8 -*-
r"""panel_scores.py · 个股评分与板块内名次轨迹（口径唯一源，2026-10-23 抽出）

背景
    这两套口径原先写在 `夏天-activity/工具/dashboard/server.py` 里（`stock_score_map` /
    `score_trail` / `sector_rank_trail`），与本文件要服务的 panel-v2 插件**必须同一份**。
    搬插件就得重写一遍 = 口径双源，所以把**计算部分**抽到本模块：

        · 取数（连库 / 缓存）留在调用方 —— 8002 旧版（2026-10-30 下线并销毁）曾用它的 `conn()`；
          panel-v2 用 `ctx.db_read()`；
        · 算什么（横截面百分位、加权、名次）只在本文件。

    本模块**不连库、不读文件、不写、不联网、不起线程**（import 期无副作用），
    符合 panel-v2 CONTRACT §8.1 口径库白名单的登记条件。

口径 1：个股评分（`score_map`）
    个股活跃市值占全市场比重，在 1/3/5/10/20 日五个窗口的变化量，
    各自做**横截面排名百分位**，按 0.15/0.20/0.25/0.25/0.15 加权成 0–100；
    `mkt_rank` = 按该评分降序的全市场名次。

口径 2：板块内名次轨迹（`rank_trail`）
    逐日算「活跃市值占全市场比重」在 period 天内的变化量，只在**本板块成分股内部**排名。

关键约束
    · `amv_by_date` / `total_by_date` 的日期键必须与 `dates` 用**同一种格式**（都 `YYYY-MM-DD`）；
    · 缺日 / 缺值一律跳过，不插值、不用 0 顶替；
    · 排序同分时的先后由调用方传入的顺序决定（保持与旧实现一致）。"""

from __future__ import annotations

from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

# 评分窗口与权重（旧 8002 `_SCORE_W` 同值，禁改）
SCORE_W: Tuple[Tuple[int, float], ...] = ((1, 0.15), (3, 0.20), (5, 0.25), (10, 0.25), (20, 0.15))
MIN_SAMPLES = 10   # 横截面样本少于此数则该窗口不计分（旧实现同值）


def score_map(
    dates_desc: Sequence[str],
    amv_by_date: Mapping[str, Mapping[str, Optional[float]]],
    total_by_date: Mapping[str, Optional[float]],
    weights: Sequence[Tuple[int, float]] = SCORE_W,
    min_samples: int = MIN_SAMPLES,
) -> Dict[str, Dict[str, float]]:
    """全市场个股评分 → `{stock: {'score': float, 'mkt_rank': int}}`。

    参数
        dates_desc    降序交易日（`dates_desc[0]` = 当日）；只用到前 `max(window)+1` 个
        amv_by_date   `{date: {stock: amv}}`（amv 单位无所谓，只算占比，两端同单位即可）
        total_by_date `{date: total}`（分母，与 amv 同单位）
        weights       `[(window_days, weight), ...]`
        min_samples   横截面最少样本数

    边界：任一窗口缺基准日、缺分母、样本不足 → 该窗口跳过（不编造）；
    全部窗口都跳过 → 返回 `{}`；`m > 1` 时百分位 `(1 - i/(m-1))*100`，`m == 1` 时取 50。
    """
    if not dates_desc:
        return {}
    acc: Dict[str, float] = {}
    for window, weight in weights:
        if window >= len(dates_desc):
            continue
        ds, base = dates_desc[0], dates_desc[window]
        tc, tb = total_by_date.get(ds), total_by_date.get(base)
        if not tc or not tb:
            continue
        cur_raw = amv_by_date.get(ds) or {}
        bse_raw = amv_by_date.get(base) or {}
        cur = {s: v / tc for s, v in cur_raw.items() if v}
        bse = {s: v / tb for s, v in bse_raw.items() if v}
        diffs = [(s, cur[s] - bse[s]) for s in cur if s in bse]
        if len(diffs) < min_samples:
            continue
        diffs.sort(key=lambda x: -x[1])
        m = len(diffs)
        for i, (stock, _v) in enumerate(diffs):
            p = (1 - i / (m - 1)) * 100 if m > 1 else 50.0
            acc[stock] = acc.get(stock, 0.0) + p * weight
    out: Dict[str, Dict[str, float]] = {}
    for rank, (stock, value) in enumerate(sorted(acc.items(), key=lambda x: -x[1]), 1):
        out[stock] = {"score": round(value, 1), "mkt_rank": rank}
    return out


def score_trail(
    dates_asc: Sequence[str],
    score_maps: Mapping[str, Mapping[str, Mapping[str, float]]],
    codes: Iterable[str],
) -> Dict[str, List[List[object]]]:
    """个股评分轨迹 → `{code: [[date, score], ...]}`（升序，只含有值的日子）。

    参数
        dates_asc   升序交易日
        score_maps  `{date: score_map(...) 的结果}`——由调用方逐日算好（本模块不回头查库）
        codes       只取这些代码
    """
    out: Dict[str, List[List[object]]] = {c: [] for c in codes}
    for d in dates_asc:
        day = score_maps.get(d) or {}
        for c in out:
            hit = day.get(c)
            if hit:
                out[c].append([d, hit["score"]])
    return out


def rank_trail(
    dates_desc: Sequence[str],
    amv_by_date: Mapping[str, Mapping[str, Optional[float]]],
    total_by_date: Mapping[str, Optional[float]],
    codes: Sequence[str],
    period: int,
    days: int = 30,
) -> Dict[str, Dict[str, object]]:
    """板块内名次轨迹 → `{code: {'sec_rank': int|None, 'trail': [[date, rank], ...]}}`。

    口径：对近 `days` 个交易日，逐日算「占全市场比重的 `period` 日变化量」，
    只在 `codes`（本板块成分）内部排名；名次 1 = 变化量最大。
    `sec_rank` = 最新一日的名次（当日没进榜则 `None`）。

    边界：`dates_desc` 长度不足 `period+1` → 返回 `{}`；某日缺分母 → 该日跳过。
    """
    code_list = list(codes)
    if not code_list or period < 1:
        return {}
    need = days + period
    dates = list(dates_desc[:need])
    if len(dates) < period + 1:
        return {}
    code_set = set(code_list)
    # 并列名次确定化：按代码升序迭代，否则 `set` 迭代顺序会让「变化量恰好相等」的股票
    # 互换名次（实测 885338 有 7 只 amv 恒 0 的票，变化量都是 0.0，名次在 1623–1629 之间乱跳）。
    # 只影响并列内部先后，不影响任何非并列名次。
    ordered = sorted(code_set)
    trail: Dict[str, List[List[object]]] = {c: [] for c in code_list}
    span = min(days, len(dates) - period)
    for i in range(span):
        d, base = dates[i], dates[i + period]
        tc, tb = total_by_date.get(d), total_by_date.get(base)
        if not tc or not tb:
            continue
        cur = amv_by_date.get(d) or {}
        bse = amv_by_date.get(base) or {}
        rows: List[Tuple[str, float]] = []
        for c in ordered:
            a1, a0 = cur.get(c), bse.get(c)
            if a1 is None or a0 is None:
                continue
            rows.append((c, a1 / tc - a0 / tb))
        if not rows:
            continue
        rows.sort(key=lambda x: -x[1])
        for k, (c, _v) in enumerate(rows, 1):
            trail[c].append([d, k])
    out: Dict[str, Dict[str, object]] = {}
    for c in code_list:
        t = trail.get(c) or []
        # `sec_rank` 取**轨迹里最近一天**的名次（轨迹升序→[0]）。若最新交易日因缺分母被跳过，
        # 这里会退回前一个交易日的名次——**与旧 8002 完全一致**（旧实现也是 `t[0][1] if t`），
        # 改它会造成 shadow 差异，且无法在无当日数据时证明哪个对。
        out[c] = {"sec_rank": t[0][1] if t else None, "trail": t[:days]}
    return out


if __name__ == "__main__":
    # 最小自检：不连库，用手算数据核对恒等关系
    dates = ["2026-01-05", "2026-01-04", "2026-01-03", "2026-01-02", "2026-01-01"]
    totals = {d: 1000.0 for d in dates}
    amv = {
        "2026-01-05": {"A": 100.0, "B": 50.0, "C": 20.0},
        "2026-01-04": {"A": 90.0, "B": 55.0, "C": 21.0},
    }
    sm = score_map(dates, amv, totals, weights=((1, 1.0),), min_samples=2)
    assert sm["A"]["mkt_rank"] == 1, sm
    assert sm["C"]["mkt_rank"] == 2, sm    # C 降幅 -0.001 > B 降幅 -0.005 → C 排前
    assert 0 <= sm["A"]["score"] <= 100

    rt = rank_trail(dates, amv, totals, ["A", "B", "C"], period=1, days=2)
    assert rt["A"]["sec_rank"] == 1, rt
    assert rt["C"]["sec_rank"] == 2, rt
    assert rt["A"]["trail"][0] == ["2026-01-05", 1], rt
    print("panel_scores self-check OK")
