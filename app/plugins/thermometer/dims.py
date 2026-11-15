# -*- coding: utf-8 -*-
"""thermometer · 四维拆解（广度 / 接力 / 资金 / 位置），纯计算 + 只读取数。

## 为什么在本插件算

四维是**新指标**，owner 就在本插件；它不替代也不重算情绪总分与恐慌广度——
总分仍在 emotion（口径 v1.1），恐慌仍在 core panic_calc。本模块只做两件事：
① 从 `track_pool.db::sentiment`（58 个收盘日）与 `kline`（000985.SZ）取原始值；
② 把每个子项转成 0–100 分位、方向统一成「越高越热」，维内等权平均。

三个上游事实由前端透传（它们各有 owner，本模块不重算）：
`promo_rate` / `promo_base` 来自 emotion payload（`today.promotion_2b` / `promotion_base`），
`panic_median_q` / `panic_amount_q` 来自 `/api/panic` 的 250 日分位（`items[].percentile`）。
缺省不传 → 该项标「缺」不参与，**绝不填 0**。

## 分位口径（与 emotion/engine.pct_rank 同式，实现独立）

跨插件不许 import（CONTRACT §8），所以这里重写了一份**同一公式**并加了对照测试
（`tests/test_thermometer_dims.py::test_pct_rank_matches_emotion_engine`，随机样本逐点比对）：
`(窗口内严格小于 + 0.5×等于) / 样本数`，窗口含当日，样本 <5 → None。

## 窗口（重要：这里是"要求 250 日、用现有样本")

主人要求 250 日滚动分位。实际可用样本：
- `sentiment` 只有 **58 个 `ts='close'` 行**（41 列里有 up_cnt/down_cnt/zt_total/dt_total/height/lb2plus/
  promotion_2b/rz_bal，没有全A中位列）→ 情绪侧子项**最多 58 日分位**，返回里带 `base` 标明实际样本数；
- 全A中位与量能只有 `/api/panic` 的 **250 日分位**（唯一源，透传使用）；
- 位置维度用 `kline`（1269 天）→ 真 250 日分位。
每个子项都带 `base`，页面与报告逐项标注，不把 58 日说成 250 日。

## 资金维度：主力资金的历史分位（2026-11-06 加）

`data/panel/index_flow_series_880008.json`（生产脚本 `夏天-activity/工具/build_index_flow_series.py`）
带 507 条 880008 全A 主力资金日序列（2024-09-30~2026-11-05，`zc_pct` = 主力净额/成交额），
所以「今天这个资金热度在历史上算高还是低」可以直接用本模块的 `pct_rank` 取 **250 日分位**，
不再只给一个孤零零的单点读数。口径是 `0.3×主力买入 + 0.7×主动买入`（L2_AMO 复刻），
**不是** eltdx `money_flow` 的原始分档——两者数字不可比、不互相校验、不混算分位：
序列可用时全用它；序列读不到才退回 eltdx 一次请求的 5 日原值，并把该项标「缺分位」。
**这份 json 是缓存，不是权威源**：序列最新日 ≠ 情绪侧当日 → 分位只展示并标「仅供参考」，
**不参与打分**（判断写死在 `build_dims` 里，不靠外部约定）。

## 方向统一（越高越热）

| 子项 | 原始方向 | 处理 |
|---|---|---|
| 上涨家数占比 / 涨停家数 / 最高板 / 连板数 / 晋级率 / 全A中位 / 量能 | 高 = 热 | 分位直接用 |
| 跌停家数 / 炸板率 | 高 = 冷 | `1 − 分位` |
| 距 MA20 / MA60 偏离、距 250 日高 | 高（偏离正、回撤浅）= 热 | 分位直接用 |

## 位置维度：数据闸门（当前不通过）

主人指定 `000985.SZ`。库内该序列实测：1269 天（2021-08-02..2026-10-29）、close 10.58–24.70、
日中位成交额 0.29 亿元、与 `000001.SH` 日收益相关 **0.36**、与 `000300.SH` 相关 0.24 ——
**不是中证全指指数序列**（量级与相关性都不对）。`000985.SH` 有 2778 天但只到 2016-06-13。

因此位置维度带**相关性闸门**：`000985.SZ` 与 `000001.SH` 日收益相关 <0.80 即判「非全指序列」，
整维标「缺」并回传体检证据（`position_probe`）。哪天真正的全指日线进库，闸门自动放行、位置自动有数，
不需要改代码；如果换代码，只改 `POSITION_CODE` 一个常量。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: 情绪侧子项的目标窗口（实际样本不足时降级并标 base）
TARGET_WINDOW = 250
#: 位置子项窗口（kline 有 1269 天，够 250）
POSITION_WINDOW = 250
#: 分位最小样本
MIN_SAMPLE = 5
#: 单维方向标签（照参考图）
DIM_BANDS = ((70.0, "过热"), (55.0, "偏热"), (40.0, "中性"), (25.0, "偏冷"), (0.0, "极冷"))
#: 位置维度的指数代码（主人 2026-10-30 定：880008 = 全A，与 880005 同日 amount 一致）
POSITION_CODE = "880008"
#: 位置序列最少根数（MA60 + 250 日窗口 + 分位样本）
POSITION_MIN_ROWS = 300
#: 两融已于 2026-10-30 整项移出资金维度（保留常量仅作历史说明）：
#: 两融是**逐票数据、无市场级接口**（880008 的 F10 rzrq：table0 空表、table1 全 None，已实测）；
#: 用 300 只沪深300 成分股聚合需 2.6 分钟，换来的边际信息量不抵成本 ⇒ 移出。


def _f(value: Any) -> Optional[float]:
    """数值化：None/空串/NaN → None（sentiment 大量字段是 NULL，不当 0 用）。"""
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def pct_rank(values: Sequence[Optional[float]], value: Optional[float],
             window: int = TARGET_WINDOW, min_sample: int = MIN_SAMPLE) -> Optional[float]:
    """value 在窗口内的分位（0–1，并列按半分）。窗口含当日，样本 <min_sample → None。"""
    v = _f(value)
    if v is None:
        return None
    sample = [x for x in (_f(y) for y in list(values)[-window:]) if x is not None]
    if len(sample) < min_sample:
        return None
    below = sum(1 for x in sample if x < v)
    equal = sum(1 for x in sample if x == v)
    return (below + 0.5 * equal) / len(sample)


def _sample_n(values: Sequence[Optional[float]], window: int = TARGET_WINDOW) -> int:
    return len([x for x in (_f(y) for y in list(values)[-window:]) if x is not None])


def band_of(score: Optional[float]) -> Optional[str]:
    """维度分 → 方向标签：极冷 <25 / 偏冷 25–40 / 中性 40–55 / 偏热 55–70 / 过热 ≥70。"""
    if score is None:
        return None
    for edge, label in DIM_BANDS:
        if score >= edge:
            return label
    return DIM_BANDS[-1][1]


# ---------------------------------------------------------------- 取数（只读）

#: 四维需要的 sentiment 列（表有 41 列，只 SELECT 这些）
SENTIMENT_COLUMNS = ("trade_date", "up_cnt", "down_cnt", "zt_total", "dt_total",
                     "zt_ever", "height", "lb2plus", "promotion_2b")


def load_sentiment(conn) -> List[Dict[str, Any]]:
    """收盘口径的 sentiment 行（升序）。只取 `ts='close'`：同一天还有 close_sc 与盘中时点行。"""
    sql = "SELECT %s FROM sentiment WHERE ts='close' ORDER BY trade_date" % ", ".join(SENTIMENT_COLUMNS)
    return [dict(r) for r in conn.execute(sql)]


# ---------------------------------------------------------------- 子项装配

def _item(key: str, label: str, raw: Any, *, direction: str, pct: Optional[float],
          base: Optional[int], source: str, unit: str = "", text: Optional[str] = None,
          used: Optional[bool] = None, why: str = "") -> Dict[str, Any]:
    """一个子项。`direction`：high_hot = 值越高越热；low_hot = 值越低越热。

    `used=None` 时按「有分位就参与」自动判；显式 False 用于样本不足/数据缺失。
    """
    p = None if pct is None else max(0.0, min(1.0, float(pct)))
    oriented = None
    if p is not None:
        oriented = p if direction == "high_hot" else 1.0 - p
    if used is None:
        used = oriented is not None
    if not used and not why:
        why = "缺原始值" if p is None else "不参与"
    return {
        "key": key,
        "label": label,
        "raw": _f(raw),
        "unit": unit,
        "text": text,
        "direction": direction,
        "percentile": None if p is None else round(p, 4),
        "oriented": None if oriented is None else round(oriented, 4),
        "base": base,
        "source": source,
        "used": bool(used),
        "why": "" if used else why,
    }


def _dim(key: str, label: str, items: List[Dict[str, Any]], note: str = "") -> Dict[str, Any]:
    """一维：有效子项等权平均（缺失跳过、按有效项归一，不用 0 顶替）。"""
    used = [it for it in items if it["used"] and it["oriented"] is not None]
    score = round(sum(it["oriented"] for it in used) / len(used) * 100.0, 1) if used else None
    missing = [it for it in items if not it["used"]]
    return {
        "key": key,
        "label": label,
        "score": score,
        "band": band_of(score),
        "n_used": len(used),
        "n_total": len(items),
        "items": items,
        "missing": [{"key": it["key"], "label": it["label"], "why": it["why"]} for it in missing],
        "note": note,
    }


# ---------------------------------------------------------------- 位置维度（kline）

def _closes(conn, code: str) -> List[Tuple[str, float]]:
    rows = conn.execute(
        "SELECT trade_date, close FROM kline WHERE ts_code=? ORDER BY trade_date", (code,)).fetchall()
    out = []
    for r in rows:
        c = _f(r[1])
        if c is not None:
            out.append((str(r[0]), c))
    return out


def _daily_returns(series: List[Tuple[str, float]]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for i in range(1, len(series)):
        prev = series[i - 1][1]
        if prev:
            out[series[i][0]] = series[i][1] / prev - 1.0
    return out


def _corr(a: Dict[str, float], b: Dict[str, float]) -> Tuple[Optional[float], int]:
    keys = sorted(set(a) & set(b))
    n = len(keys)
    if n < 30:
        return None, n
    xs = [a[k] for k in keys]
    ys = [b[k] for k in keys]
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / n
    sx = (sum((x - mx) ** 2 for x in xs) / n) ** 0.5
    sy = (sum((y - my) ** 2 for y in ys) / n) ** 0.5
    if not sx or not sy:
        return None, n
    return cov / (sx * sy), n


def _moving_avg(closes: List[float], n: int) -> Optional[float]:
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / n


def position_series(closes: List[float],
                     highs: Optional[List[Optional[float]]] = None) -> Dict[str, List[Optional[float]]]:
    """逐日的三个位置指标（与当日最右对齐）：MA20 偏离%、MA60 偏离%、距 250 日高。

    **250 日高点取最高价 `high`（2026-10-30 主人定）**：`high_250 = max(high[i-249..i])`，
    回撤 = (close_t - high_250) / high_250。用收盘价算会系统性低估回撤（2026-10-29 实测差 0.26pp）。
    `highs` 缺失/该位为空时退回收盘价，并在返回值里不另做标记（由调用方决定是否带 high）。
    """
    dev20: List[Optional[float]] = []
    dev60: List[Optional[float]] = []
    gap250: List[Optional[float]] = []
    hs = list(highs) if highs is not None else []
    for i in range(len(closes)):
        window = closes[: i + 1]
        ma20 = _moving_avg(window, 20)
        ma60 = _moving_avg(window, 60)
        c = closes[i]
        dev20.append(None if not ma20 else c / ma20 - 1.0)
        dev60.append(None if not ma60 else c / ma60 - 1.0)
        lo = max(0, i - POSITION_WINDOW + 1)
        tail = closes[lo: i + 1]
        top = max(tail) if tail else None
        if len(hs) > i and hs[i] is not None:
            hi_tail = [h for h in hs[lo: i + 1] if h is not None]
            if hi_tail:
                top_high = max(hi_tail)
                top = max(top, top_high) if top is not None else top_high
        gap250.append(None if not top else c / top - 1.0)
    return {"dev20": dev20, "dev60": dev60, "gap250": gap250}


def load_index_series(path) -> Dict[str, Any]:
    """读 880008 全A指数日线（生产脚本 `tools/index_880008_daily.py` 的产物）。

    vipdoc 历史 + eltdx 尾部合并；`meta` 里同时保存 vipdoc_last / eltdx_last / merged_last，
    插件据此判「尾部补齐到底补没补到今日」，不把 vipdoc 停更日当今天。
    """
    if path is None or not getattr(path, "is_file", lambda: False)():
        return {"rows": [], "meta": {}, "reason": "位置序列文件不存在（先跑 tools/index_880008_daily.py）"}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"rows": [], "meta": {}, "reason": "位置序列读取失败：%s" % exc}
    rows = [r for r in (doc.get("series") or []) if _f(r.get("close"))]
    rows.sort(key=lambda r: str(r.get("date")))
    return {"rows": rows, "meta": doc.get("meta") or {}, "reason": ""}


def position_block(index: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Dict[str, Any], Optional[str]]:
    """位置维度（880008 全A）：距 MA20 / MA60 偏离 + 距 250 日高回撤，各取 250 日分位。

    `index` = `load_index_series()` 的结果；`asof` 由 build_dims 传入后比对（见下方 fresh 判断）。
    """
    rows = list((index or {}).get("rows") or [])
    meta = (index or {}).get("meta") or {}
    source = "880008（vipdoc+eltdx）→ data/panel_v2/thermometer/index_880008_series.json"
    probe = {
        "code": POSITION_CODE,
        "rows": len(rows),
        "first": rows[0]["date"] if rows else None,
        "last": rows[-1]["date"] if rows else None,
        "vipdoc_last": meta.get("vipdoc_last"),
        "eltdx_last": meta.get("eltdx_last"),
        "merged_last": meta.get("merged_last"),
        "overlap_max_diff": meta.get("overlap_max_diff"),
        "fresh": None,
        "min_rows": POSITION_MIN_ROWS,
        "reason": "",
    }

    def blank(reason: str, short: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Dict[str, Any], Optional[str]]:
        probe["reason"] = reason
        items = [
            _item("dev20", "距 MA20 偏离", None, direction="high_hot", pct=None, base=None,
                  source=source, unit="%", used=False, why=reason),
            _item("dev60", "距 MA60 偏离", None, direction="high_hot", pct=None, base=None,
                  source=source, unit="%", used=False, why=reason),
            _item("gap250", "距 250 日高回撤", None, direction="high_hot", pct=None, base=None,
                  source=source, unit="%", used=False, why=reason),
        ]
        return _dim("position", "位置", items, note=short), items, probe, probe["last"]

    if len(rows) < POSITION_MIN_ROWS:
        return blank("880008 序列仅 %d 根（< %d），不够算 MA60 与 250 日分位" % (len(rows), POSITION_MIN_ROWS),
                     "缺：位置序列仅 %d 根" % len(rows))

    closes = [float(r["close"]) for r in rows]
    highs = [None if r.get("high") is None else float(r["high"]) for r in rows]
    hist = position_series(closes, highs)
    base = min(POSITION_WINDOW, len(closes))
    today = rows[-1]["date"]
    items = [
        _item("dev20", "距 MA20 偏离", None if hist["dev20"][-1] is None else hist["dev20"][-1] * 100,
              direction="high_hot", pct=pct_rank(hist["dev20"], hist["dev20"][-1], POSITION_WINDOW),
              base=_sample_n(hist["dev20"], POSITION_WINDOW), source=source, unit="%"),
        _item("dev60", "距 MA60 偏离", None if hist["dev60"][-1] is None else hist["dev60"][-1] * 100,
              direction="high_hot", pct=pct_rank(hist["dev60"], hist["dev60"][-1], POSITION_WINDOW),
              base=_sample_n(hist["dev60"], POSITION_WINDOW), source=source, unit="%"),
        _item("gap250", "距 250 日高回撤", None if hist["gap250"][-1] is None else hist["gap250"][-1] * 100,
              direction="high_hot", pct=pct_rank(hist["gap250"], hist["gap250"][-1], POSITION_WINDOW),
              base=_sample_n(hist["gap250"], POSITION_WINDOW), source=source, unit="%"),
    ]
    for it in items:
        it["asof"] = today
    return _dim("position", "位置", items, note="%s · %d 日分位" % (POSITION_CODE, base)), items, probe, today


# ---------------------------------------------------------------- 资金维度两个新数据源（只读）

#: 龙虎榜缓存文件名（hithink 抓取产物，只读）
DRAGON_RE = "hithink_%s_dragon.json"
#: 主力资金历史序列文件名（`build_index_flow_series.py` 的产物，落在 data/panel/）
FLOW_SERIES_FILE = "index_flow_series_880008.json"


def parse_index_flow_series(doc) -> Dict[str, Any]:
    """把已解析的序列文档规整成 `{rows, meta, latest, reason}`（纯函数，不碰 IO）。

    生产路径走 core 的 panel 白名单（`ctx.panel_json("index_flow_series_880008")` →
    本函数），`load_index_flow_series()` 只是按路径读的薄封装（测试 / 离线校验用）。
    """
    if not isinstance(doc, dict):
        return {"rows": [], "meta": {}, "latest": {}, "reason": "主力资金序列顶层不是对象"}
    rows = []
    for r in (doc.get("series") or []):
        if not isinstance(r, dict) or not r.get("date"):
            continue
        zc = _f(r.get("zc_pct"))
        net = _f(r.get("main_net_yi"))
        if zc is None and net is None:
            continue
        rows.append({
            "date": str(r["date"]),
            "zc_pct": zc,
            "main_net_yi": net,
            "amount_yi": _f(r.get("amount_yi")),
        })
    rows.sort(key=lambda x: x["date"])
    meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
    latest = doc.get("latest") if isinstance(doc.get("latest"), dict) else {}
    return {"rows": rows, "meta": meta, "latest": latest,
            "reason": "" if rows else "序列为空"}


def load_index_flow_series(path) -> Dict[str, Any]:
    """按路径读 880008 的 L2 主力资金历史序列（`build_index_flow_series.py` 的产物）。

    口径唯一源 = 生成脚本文件头：`0.3×主力买入 + 0.7×主动买入`（L2_AMO 复刻），
    **不是** eltdx `money_flow` 的原始分档，两套数字不可比、不得互相校验（见模块文档）。

    生产不走这里（走 `ctx.panel_json("index_flow_series_880008")` + `parse_index_flow_series`）；
    本函数留给测试与离线校验。文件不存在/不是 JSON/顶层不是对象 → 空 rows + reason
    （调用方据此降级回实时请求并标「缺分位」，**不编**）。
    """
    if path is None:
        return {"rows": [], "meta": {}, "latest": {}, "reason": "未提供主力资金序列路径"}
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"rows": [], "meta": {}, "latest": {},
                "reason": "主力资金序列不存在：%s" % Path(path).name}
    except (OSError, ValueError) as exc:
        return {"rows": [], "meta": {}, "latest": {},
                "reason": "%s: %s" % (type(exc).__name__, exc)}
    return parse_index_flow_series(doc)


def load_dragon_series(cache_dir) -> Dict[str, Any]:
    """读龙虎榜缓存（hithink `dragon`），逐日汇总净买额。

    口径：`net_value` = 该股当日龙虎榜净买额（元，hithink 原值）；日值 = 全部股票求和。
    缺失字段不补 0（无 net_value 的股不计入，并用 n_net/n 落证）。
    """
    rows: List[Dict[str, Any]] = []
    if cache_dir is None:
        return {"rows": rows, "reason": "未提供缓存目录"}
    try:
        files = sorted(Path(cache_dir).glob("hithink_*_dragon.json"))
    except OSError as exc:
        return {"rows": rows, "reason": "%s: %s" % (type(exc).__name__, exc)}
    for f in files:
        m = re.search(r"hithink_(\d{8})_dragon\.json$", f.name)
        if not m:
            continue
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
            items = doc["data"]["data"].get("stock_items") or []
        except (OSError, ValueError, KeyError, TypeError):
            continue
        nets = [x.get("net_value") for x in items if isinstance(x, dict)]
        nets = [float(v) for v in nets if isinstance(v, (int, float))]
        rows.append({
            "date": m.group(1),
            "net_sum": sum(nets) if nets else None,
            "n": len(items),
            "n_net": len(nets),
        })
    rows.sort(key=lambda x: x["date"])
    return {"rows": rows, "reason": "" if rows else "%s 下无 hithink_*_dragon.json" % cache_dir}


# ---------------------------------------------------------------- 三个横截面维度

def _sentiment_items(rows: List[Dict[str, Any]]) -> Dict[str, List[Optional[float]]]:
    """sentiment 收盘行 → 各子项的原始序列（升序）。"""
    def col(name):
        return [_f(r.get(name)) for r in rows]

    up = col("up_cnt")
    down = col("down_cnt")
    ratio = []
    for u, d in zip(up, down):
        ratio.append(None if (u is None or d is None or (u + d) == 0) else u / (u + d))
    zt = col("zt_total")
    ever = col("zt_ever")
    burst = []
    for a, b in zip(zt, ever):
        burst.append(None if (a is None or not b) else (b - a) / b)
    return {
        "up_ratio": ratio,
        "zt_total": zt,
        "dt_total": col("dt_total"),
        "height": col("height"),
        "lb2plus": col("lb2plus"),
        "burst": burst,
        "promotion": col("promotion_2b"),
        "trade_date": [str(r.get("trade_date") or "") for r in rows],
    }


def build_dims(rows: List[Dict[str, Any]], index: Optional[Dict[str, Any]] = None, *,
               promo_rate: Optional[float] = None,
               promo_base: Optional[int] = None, panic_median_q: Optional[float] = None,
               panic_amount_q: Optional[float] = None, panic_median: Optional[float] = None,
               panic_amount: Optional[float] = None,
               flow_series: Optional[Dict[str, Any]] = None,
               index_flow: Optional[Dict[str, Any]] = None,
               dragon: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """四维装配。`rows` = sentiment 收盘行（升序）；`index` = 880008 日线（`load_index_series`）。

    `panic_median` / `panic_amount` 是上游的**原始值**（全A中位 %、量能比 ×），
    `panic_*_q` 是同两项的 250 日分位。两者都由前端从 `/api/panic` 透传。
    `flow_series` = 880008 的 L2 主力资金**历史序列**（`load_index_flow_series`，507 条，有 250 日分位）；
    `index_flow` = 同指数 eltdx 一次请求的 5 日原值（**降级路径**：只在序列读不到时用，不分位）；
    `dragon` 是本插件唯一的只读序列（见 `load_dragon_series`）；两融已移出资金维度。
    """
    ser = _sentiment_items(rows)
    n = len(rows)
    window = min(TARGET_WINDOW, n)
    asof = ser["trade_date"][-1] if ser["trade_date"] else None

    def last(name):
        vals = ser[name]
        return vals[-1] if vals else None

    # ---- ① 广度：上涨家数占比 / 涨停 / 跌停 / 全A中位
    breadth_items = [
        _item("up_ratio", "上涨家数占比", None if last("up_ratio") is None else last("up_ratio") * 100,
              direction="high_hot", pct=pct_rank(ser["up_ratio"], last("up_ratio"), window),
              base=_sample_n(ser["up_ratio"], window), source="sentiment:up_cnt/down_cnt", unit="%"),
        _item("zt_total", "涨停家数", last("zt_total"), direction="high_hot",
              pct=pct_rank(ser["zt_total"], last("zt_total"), window),
              base=_sample_n(ser["zt_total"], window), source="sentiment:zt_total", unit="家"),
        _item("dt_total", "跌停家数", last("dt_total"), direction="low_hot",
              pct=pct_rank(ser["dt_total"], last("dt_total"), window),
              base=_sample_n(ser["dt_total"], window), source="sentiment:dt_total", unit="家"),
        _item("median_pct", "全A中位涨跌", panic_median, direction="high_hot",
              pct=None if panic_median_q is None else float(panic_median_q) / 100.0,
              base=None if panic_median_q is None else 250,
              source="panic:items.median", unit="%",
              used=panic_median_q is not None,
              why="上游 /api/panic 未提供（250 日分位）" if panic_median_q is None else ""),
    ]
    dims = [_dim("breadth", "广度", breadth_items,
                 note="%d 日基（sentiment 收盘行现 %d 行；全A中位为 panic 250 日基）" % (window, n))]

    # ---- ② 接力：最高板 / 连板 / 晋级率 / 炸板率
    promo_item = _item("promotion", "晋级率（2→3 板）",
                       None if promo_rate is None else float(promo_rate) * 100,
                       direction="high_hot",
                       pct=None if promo_rate is None else pct_rank(ser["promotion"], float(promo_rate) * 100.0, window),
                       base=_sample_n(ser["promotion"], window), source="sentiment:promotion_2b", unit="%")
    # 分母样本（昨日 2 板家数）由 emotion 透传；它是「样本是否足够」的唯一判据
    promo_item["denominator"] = promo_base
    if promo_base is not None and promo_base < 10:
        promo_item["used"] = False
        promo_item["why"] = "样本不足：昨日 2 板仅 %d 只（<10），不给分" % promo_base
    elif promo_rate is None:
        promo_item["used"] = False
        promo_item["why"] = "上游 emotion 未提供晋级率（无样本）"
    relay_items = [
        _item("height", "最高板", last("height"), direction="high_hot",
              pct=pct_rank(ser["height"], last("height"), window),
              base=_sample_n(ser["height"], window), source="sentiment:height", unit="板"),
        _item("lb2plus", "连板数（2 板+）", last("lb2plus"), direction="high_hot",
              pct=pct_rank(ser["lb2plus"], last("lb2plus"), window),
              base=_sample_n(ser["lb2plus"], window), source="sentiment:lb2plus", unit="家"),
        promo_item,
        # 炸板率：只在库里自算偏高 ~7.5pp，故**只出分位、不出绝对值**（text 为空）
        _item("burst", "炸板率（只看分位）", None, direction="low_hot",
              pct=pct_rank(ser["burst"], last("burst"), window),
              base=_sample_n(ser["burst"], window), source="sentiment:zt_ever/zt_total",
              text="%d 日分位（绝对值不可比，自算偏高）" % window),
    ]
    dims.append(_dim("relay", "接力", relay_items, note="晋级率分母 <10 只时不参与"))

    # ---- ③ 资金：量能（250 日基）+ 主力资金（880008）+ 龙虎榜 + 北向（停披露）
    #
    # 主力资金两个来源，按优先级用**一个**，绝不混算：
    #   ① `flow_series` = 880008 的 L2 历史序列（data/panel/index_flow_series_880008.json，507 条）
    #      → 占成交 % 取 250 日分位，参与维度打分；
    #   ② 序列读不到（缺文件/坏文件/为空）→ 退回 eltdx `money_flow.daily` 一次请求的 5 日原值
    #      → 只展示读数、标「缺分位」，**不参与打分**（维度不因它整维空掉）。
    # 两套数字不可比：① 是 `0.3×主力买入 + 0.7×主动买入`（L2_AMO 复刻），② 是 eltdx 自己的分档；
    # 分位只在 ① 内部算，不拿 ② 的几天去凑窗口。
    fs_rows = [r for r in ((flow_series or {}).get("rows") or []) if r.get("zc_pct") is not None]
    fs_rows.sort(key=lambda r: str(r.get("date")))
    flow_pct = None
    flow_base = None
    if fs_rows:
        fs_vals = [r["zc_pct"] for r in fs_rows]
        fs_last = fs_rows[-1]
        flow_ratio = _f(fs_last.get("zc_pct"))
        net_yi = _f(fs_last.get("main_net_yi"))
        flow_net = None if net_yi is None else net_yi * 1e8
        flow_date = str(fs_last.get("date"))
        flow_pct = pct_rank(fs_vals, flow_ratio, TARGET_WINDOW)
        flow_base = _sample_n(fs_vals, TARGET_WINDOW)
        # 5 日对比：新→旧（字段名沿用 eltdx 路径的 main_ratio / main_net，单位仍是 % / 元）
        flow_hist = [{"date": str(r["date"]), "main_ratio": _f(r.get("zc_pct")),
                      "main_net": None if r.get("main_net_yi") is None else r["main_net_yi"] * 1e8}
                     for r in reversed(fs_rows[-5:])]
        flow_src = ("L2 序列 %d 条（%s~%s，data/panel/%s，L2_AMO 复刻口径）"
                    % (len(fs_rows), fs_rows[0]["date"], flow_date, FLOW_SERIES_FILE))
    else:
        elt_rows = [r for r in ((index_flow or {}).get("rows") or []) if r.get("main_ratio") is not None]
        elt_rows.sort(key=lambda r: str(r.get("date")))
        elt_last = elt_rows[-1] if elt_rows else None
        flow_ratio = None if elt_last is None else _f(elt_last.get("main_ratio"))
        flow_net = None if elt_last is None else _f(elt_last.get("main_net"))
        flow_date = None if elt_last is None else str(elt_last.get("date"))
        flow_hist = [{"date": str(r.get("date")), "main_ratio": _f(r.get("main_ratio")),
                      "main_net": _f(r.get("main_net"))} for r in reversed(elt_rows)]
        flow_src = "eltdx money_flow.daily('sh880008')（降级：一次请求，5 日原值）"
    flow_text = None
    if flow_ratio is not None:
        bits = []
        if flow_net is not None:
            bits.append("主力净额 %+.2f 亿" % (flow_net / 1e8))
        if len(flow_hist) > 1:
            bits.append("5 日占成交（新→旧）%s%%" % " / ".join(
                "%+.2f" % h["main_ratio"] for h in flow_hist if h["main_ratio"] is not None))
        bits.append(flow_src)
        flow_text = " · ".join(bits)

    flow_used = False
    # 新鲜度判断（**写死在这里，不靠约定**）：序列最新日 !== 情绪侧 asof → 分位只展示、不参与打分。
    # 理由：本文件是 `.cah` 的**缓存**（覆盖写、可重建），没刷新就是旧数据；旧数据不能冒充当日参与打分。
    flow_stale = bool(flow_ratio is not None and flow_pct is not None
                      and not (flow_date and asof and str(flow_date) == str(asof)))
    flow_why = ""
    if flow_ratio is None:
        flow_why = "缺分位：%s" % (((flow_series or {}).get("reason") or (index_flow or {}).get("reason")
                                 or "主力资金序列与实时请求都没数"))
    elif flow_pct is None:
        flow_why = "缺分位：序列不足 5 日，不拿单点值当分位"
    elif flow_stale:
        flow_why = ("序列陈旧（最新 %s，情绪侧 %s）：分位仅供参考，不参与打分"
                    % (flow_date or "缺", asof or "缺"))
    else:
        flow_used = True

    flow_item = _item(
        "main_ratio", "主力资金（880008 占成交）",
        flow_ratio,
        direction="high_hot",
        pct=flow_pct,
        base=flow_base,
        source="%s · 占成交%% = 主力净额/成交额 · 250 日分位" % flow_src if flow_pct is not None else flow_src,
        unit="%",
        text=flow_text,
        used=flow_used,
        why=flow_why,
    )
    flow_item["asof"] = flow_date
    flow_item["main_net_yi"] = None if flow_net is None else round(flow_net / 1e8, 2)
    flow_item["history"] = flow_hist
    flow_item["source_kind"] = "series" if fs_rows else "live"
    flow_item["stale"] = flow_stale      # 供前端在分位旁标「参考」


    dg_rows = list((dragon or {}).get("rows") or [])
    dg_vals = [r["net_sum"] for r in dg_rows if r.get("net_sum") is not None]
    dg_today = dg_vals[-1] if dg_rows and dg_rows[-1].get("net_sum") is not None else None
    dg_date = dg_rows[-1]["date"] if dg_rows else None
    dg_item = _item(
        "dragon", "龙虎榜净买额（全市场合计）",
        None if dg_today is None else dg_today / 1e8,
        direction="high_hot",
        pct=None if dg_today is None else pct_rank(dg_vals, dg_today, TARGET_WINDOW),
        base=len([v for v in dg_vals if v is not None]) or None,
        source="xiaoshi_cache hithink_*_dragon.json（net_value 汇总）",
        unit="亿",
        used=None if dg_today is not None else False,
        why=(dragon or {}).get("reason") or "缓存无当日文件",
    )
    dg_item["asof"] = dg_date
    dg_item["sample_n"] = dg_rows[-1]["n"] if dg_rows else None

    # 两融已于 2026-10-30 整项移出资金维度（逐票数据、无市场级接口，详见本模块常量注释与 README）

    capital_items = [
        _item("amount", "量能（成交额/昨日）", panic_amount, direction="high_hot",
              pct=None if panic_amount_q is None else float(panic_amount_q) / 100.0,
              base=None if panic_amount_q is None else 250,
              source="panic:items.amount", unit="×",
              used=panic_amount_q is not None,
              why="上游 /api/panic 未提供（250 日分位）" if panic_amount_q is None else ""),
        flow_item,
        dg_item,
        # 北向：官方自 2024-08 停披露日频金额，只剩季度持仓 → 不做日频资金项，显式标缺
        _item("northbound", "北向净流入（日频）", None, direction="high_hot", pct=None, base=None,
              source="东财 datacenter / eltdx f10.northbound_holding", unit="亿",
              text="仅季度持仓 RPT_MUTUAL_HOLDSTOCKNORTH_STA（2026-09-30）",
              used=False,
              why="官方停披露：北向日频金额自 2024-08 起全 null（只剩季度持仓）"),
    ]
    used_n = sum(1 for it in capital_items if it["used"])
    cap_bits = ["%d/%d 项参与" % (used_n, len(capital_items))]
    if flow_ratio is None:
        cap_bits.append("主力资金缺")
    elif flow_stale:
        cap_bits.append("主力资金序列陈旧（分位仅供参考）")
    elif not flow_used:
        cap_bits.append("主力资金缺分位")
    else:
        cap_bits.append("主力资金 %d 日分位" % flow_base if flow_base else "主力资金有分位")
    cap_bits.append("北向官方停披露")
    cap_bits.append("两融已移出（逐票无市场级接口）")
    cap_dim = _dim("capital", "资金", capital_items, note=" · ".join(cap_bits))
    # 5 日对比写在格子下方那行（不另占高度）：新→旧的占成交与当日净额
    if flow_ratio is not None and len(flow_hist) > 1:
        cap_dim["flow_compare"] = "5日占成交 " + "/".join(
            "%+.2f" % h["main_ratio"] for h in flow_hist if h["main_ratio"] is not None) + "%（新→旧）"
        if flow_net is not None:
            cap_dim["flow_compare"] += " · 净额 %+.2f 亿" % (flow_net / 1e8)
    cap_dim["flow_asof"] = flow_date
    cap_dim["flow_source"] = "series" if fs_rows else "live"
    cap_dim["flow_stale"] = flow_stale
    dims.append(cap_dim)

    # ---- ④ 位置（880008 全A）：序列必须新鲜到 asof，否则不拿旧点位冒充今日
    pos_dim, pos_items, probe, pos_asof = position_block(index)
    probe["asof"] = asof
    probe["fresh"] = bool(pos_asof and asof and str(pos_asof) == str(asof))
    if pos_items and pos_items[0]["used"] and not probe["fresh"]:
        why = "位置序列最新 %s，缺至 %s（不拿旧点位冒充今日）" % (pos_asof or "缺", asof or "缺")
        probe["reason"] = why
        for it in pos_items:
            it["used"] = False
            it["why"] = why
        pos_dim = _dim("position", "位置", pos_items, note=why)
    dims.append(pos_dim)

    missing = []
    for d in dims:
        for m in d["missing"]:
            missing.append({"dim": d["key"], "dim_label": d["label"], "key": m["key"],
                            "label": m["label"], "why": m["why"]})

    return {
        "ok": True,
        "asof": asof,
        "sentiment_rows": n,
        "sentiment_window": window,
        "target_window": TARGET_WINDOW,
        "position_asof": pos_asof,
        "dims": dims,
        "missing": missing,
        "position_probe": probe,
        "caliber": {
            "score": "子项 0–100 分位，方向统一为越高越热；维内等权平均，缺失跳过并按有效项归一",
            "window": ("目标 250 日；sentiment 收盘行仅 %d 行 → 情绪侧实际 %d 日基；"
                       "全A中位/量能用 panic 的 250 日分位；位置用 880008 的 250 日分位；"
                       "主力资金用 L2 历史序列的 250 日分位" % (n, window)),
            "capital": ("资金四项：量能（panic 250 日基）/ 主力资金（880008 占成交，L2 历史序列 250 日分位；"
                        "序列读不到才退回 eltdx 一次请求并标缺分位）/ 龙虎榜净买额（hithink 缓存 %d 日基）"
                        "/ 北向（官方停披露）（两融已于 2026-10-30 移出：逐票数据、无市场级接口）"
                        % (len(dg_vals),)),
            "main_flow": ("来源优先级 ① data/panel/%s（生产脚本 夏天-activity/工具/build_index_flow_series.py；"
                          "L2 .cah × l2_mf 公式，%d 条）→ 主值 zc_pct（占成交 %%）取 250 日分位"
                          "（严格小于 + 0.5×等于，样本 <5 → 缺）；② 序列不可读 → eltdx money_flow.daily('sh880008')"
                          "（一次请求 5 条，只展示读数、标缺分位）。两套口径不可比、不互相校验、不混算分位；"
                          "序列最新日 ≠ 情绪侧 asof → 分位只标「仅供参考」、不参与打分"
                          % (FLOW_SERIES_FILE, len(fs_rows))),
            "relay_burst": "炸板率只用分位、不展示绝对值（库里自算偏高约 7.5pp）",
            "no_composite": "四维之间不加权合成，各自独立展示；情绪总分仍由 emotion 提供",
            "position_gate": ("位置用 880008 全A（vipdoc 历史 + eltdx 尾部补齐）；序列最新日必须等于情绪侧 "
                              "asof 才参与，否则整维标缺（不拿旧点位冒充今日）"),
        },
    }
