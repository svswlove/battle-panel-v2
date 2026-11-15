# -*- coding: utf-8 -*-
"""emotion 插件 · 取数层（只读；不联网、不写任何数据源）。

## 两个数据源

| 源 | 用途 | 访问方式 |
|---|---|---|
| `track_pool.db::sentiment`（`ts='close'`） | 六因子的原始值 + `stage`/`stage_note` | `ctx.db_read("track_pool.db")`（core 只读白名单） |
| `夏天-activity/工具/xiaoshi_cache/hithink_{date}_limitup.json` / `_ladder.json` | 晋级率回填（历史涨停池 + 连板梯队） | 只读文件（本插件自己推导路径，同 valuation 先例） |

`sector_ladder_{date}.json`（`data/panel/`）只做**交叉校验**，不参与打分主体：
`limitup_total` 对 `sentiment.zt_total` 逐日比对，不一致记进 `payload.checks`（不改任何一边）。

## 晋级率怎么算（口径）

`sentiment.promotion_2b/3b/4b/5b` 原本由 `夏天-activity/工具/derive_sentiment_metrics.py` 用妙想 MCP
查询写入（脚本 2026-09-13 停用，字段全 None）。本模块用**本地涨停池**复算同一含义：

    晋级率_N = |昨日 N 板股票 ∩ 今日 (N+1) 板股票| / |昨日 N 板股票|

- 「昨日 N 板」= 昨日涨停池里 `continue_day_cnt == N` 的股票（`hithink_{date}_limitup.json`）；
- 「今日 N+1 板」= 今日涨停池里 `continue_day_cnt == N+1` 的股票；
- 分母为 0 → 该板晋级率 None（不是 0：没有样本 ≠ 全员失败）。

与旧 MCP 口径的差别：旧口径是「昨日连板 N 且今日涨停」（不校验今日板数），数据源是东财。
本地复算在 2026-10-13 与库内旧值完全一致（35.71% = 5/14），2026-10-08 不同（66.67% vs 50.00%）。
因此**库内已有值优先**（`source="db"`，原口径），缺失时才用复算值（`source="limitup"`），
每个点带来源标记，前端可辨。**不写回 `track_pool.db`**：那是生产表，插件只读。

`premium`（昨日涨停股今日平均涨幅）本地无源：需要昨日涨停股**今日**的全A行情快照，
`xiaoshi_cache` 只有涨停股自身、`data/panel` 无全A收盘快照。因此明确标注不可用，不编。
"""
from __future__ import annotations

import importlib.util as _ilu
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.core import paths

# 兄弟模块加载（与 concept / paper 插件同款三级兜底）：生产时 core 用
# `spec_from_file_location("_panel_v2_plugin_emotion_backend", ...)` 加载 backend，本模块没有父包，
# 相对导入不存在；pytest 里走 `app.plugins.emotion` 包导入。
try:                                        # 1) 包导入（pytest / 其他模块 import）
    from . import engine  # type: ignore
except ImportError:                         # pragma: no cover - core 按文件加载时走这里
    try:                                    # 2) 项目根在 sys.path 时的绝对导入
        from app.plugins.emotion import engine  # type: ignore
    except ImportError:                     # 3) 按路径加载同目录兄弟模块
        _here = Path(__file__).resolve().parent
        _spec = _ilu.spec_from_file_location("_panel_v2_emotion_engine", _here / "engine.py")
        if _spec is None or _spec.loader is None:
            raise ImportError("无法加载 emotion 插件的 engine 模块")
        engine = _ilu.module_from_spec(_spec)  # type: ignore
        sys.modules[_spec.name] = engine
        _spec.loader.exec_module(engine)

#: 涨停池/梯队缓存目录（妙想抓取产物，只读）
CACHE_DIRNAME = "xiaoshi_cache"
#: 涨停池文件名模式
LIMITUP_RE = re.compile(r"hithink_(\d{8})_limitup\.json$")
LADDER_RE = re.compile(r"hithink_(\d{8})_ladder\.json$")
#: 板块涨停梯队（data/panel，只读）
SECTOR_LADDER_RE = re.compile(r"sector_ladder_(\d{8})\.json$")

#: 参与打分的历史板块（2~5 板晋级率）
PROMOTION_LEVELS = (2, 3, 4, 5)
#: 晋级率小样本下限（与 engine 同值；分母 < 该值不给分）
MIN_PROMOTION_SAMPLE = engine.MIN_PROMOTION_SAMPLE

#: 文件缓存存活秒数（日频数据；避免每次请求重读 50+ 个小 JSON）
CACHE_TTL_S = 180.0
_FILE_CACHE: Dict[str, Any] = {}


def cache_dir() -> Path:
    """涨停池缓存目录。调用时求值，便于测试 monkeypatch。"""
    return Path(paths.SUMMER_TOOLS_DIR) / CACHE_DIRNAME


def panel_dir() -> Path:
    """`data/panel` 目录（板块梯队产物所在）。调用时求值。"""
    return Path(paths.DATA_DIR) / "panel"


def _dir_signature(base: Path, pattern: "re.Pattern[str]") -> Tuple[Any, ...]:
    """目录签名：命中文件数 + 最大 mtime。文件增删或改写即视为过期。"""
    if not base.is_dir():
        return (0, 0.0)
    count, newest = 0, 0.0
    for name in os.listdir(base):
        if not pattern.search(name):
            continue
        count += 1
        try:
            newest = max(newest, (base / name).stat().st_mtime)
        except OSError:
            continue
    return (count, round(newest, 3))


# ---------------------------------------------------------------- sentiment

#: 打分与展示需要的 sentiment 列（只 SELECT 这些，不 select *：表有 41 列）
_SENTIMENT_COLUMNS = (
    "trade_date", "zt_total", "zt_ever", "dt_total", "height", "lb2plus",
    "up_cnt", "down_cnt", "reseal_rate", "stage", "stage_note",
    "promotion_2b", "promotion_3b", "promotion_4b", "promotion_5b", "premium",
)


def load_sentiment(conn) -> List[Dict[str, Any]]:
    """收盘口径的 sentiment 行，按 trade_date 升序。

    只取 `ts='close'`：同一天还有 `close_sc`（另一套收盘口径）与盘中时点行（`0954`/`1456`…），
    混进来会让「一个交易日」出现多个分数。这是 `derive_sentiment_metrics.py` 的既有口径。
    """
    sql = "SELECT %s FROM sentiment WHERE ts='close' ORDER BY trade_date" % ", ".join(_SENTIMENT_COLUMNS)
    return [dict(r) for r in conn.execute(sql)]


# ---------------------------------------------------------------- 涨停池 / 梯队

def _read_json(path: Path) -> Optional[Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _limitup_levels(payload: Any) -> Optional[Dict[int, set]]:
    """从 `hithink_*_limitup.json` 取 {连板数: {代码}}。结构不符返回 None。"""
    try:
        items = payload["data"]["data"]["item"]
    except (TypeError, KeyError):
        return None
    levels: Dict[int, set] = {}
    for row in items or []:
        code = row.get("thscode") or row.get("ticker")
        cnt = row.get("continue_day_cnt")
        if not code or not isinstance(cnt, (int, float)):
            continue
        levels.setdefault(int(cnt), set()).add(code)
    return levels


def _ladder_levels(payload: Any) -> Optional[Dict[int, set]]:
    """从 `hithink_*_ladder.json` 取当日 {连板数: {代码}}（文件带 30 天窗口，只取 date_list[0] 那条）。"""
    try:
        inner = payload["data"]["data"]
        date_list = inner["window"]["date_list"]
        items = inner["item"]
    except (TypeError, KeyError):
        return None
    today = date_list[0] if date_list else None
    levels: Dict[int, set] = {}
    for row in items or []:
        if today and row.get("date") != today:
            continue
        for board in (row.get("boards") or {}).values():
            if not isinstance(board, list):
                continue
            for stock in board:
                code = stock.get("thscode") or stock.get("ticker")
                num = stock.get("board_num")
                if not code or not isinstance(num, (int, float)):
                    continue
                levels.setdefault(int(num), set()).add(code)
    return levels


def load_pool() -> Tuple[Dict[str, Dict[int, set]], Dict[str, str]]:
    """加载全部涨停池：{YYYYMMDD: {连板数: {代码}}} + 每日来源。

    优先 `limitup`（全池，35 个文件）；缺失日期用 `ladder`（30 天窗口，只有 2 板以上）补。
    """
    base = cache_dir()
    sig = _dir_signature(base, LIMITUP_RE) + _dir_signature(base, LADDER_RE)
    cached = _FILE_CACHE.get("pool")
    if cached and cached[0] == sig and (time.time() - cached[1]) < CACHE_TTL_S:
        return cached[2]

    pool: Dict[str, Dict[int, set]] = {}
    source: Dict[str, str] = {}
    if base.is_dir():
        for name in sorted(os.listdir(base)):
            m = LIMITUP_RE.search(name)
            if not m:
                continue
            levels = _limitup_levels(_read_json(base / name))
            if levels:
                pool[m.group(1)] = levels
                source[m.group(1)] = "limitup"
        for name in sorted(os.listdir(base)):
            m = LADDER_RE.search(name)
            if not m or m.group(1) in pool:
                continue
            levels = _ladder_levels(_read_json(base / name))
            if levels:
                pool[m.group(1)] = levels
                source[m.group(1)] = "ladder"
    _FILE_CACHE["pool"] = (sig, time.time(), (pool, source))
    return pool, source


def derive_promotions(pool: Dict[str, Dict[int, set]]) -> Dict[str, Dict[str, Any]]:
    """逐日推算 2~5 板晋级率。分母 0 或首日无「昨日」→ 该板 None。"""
    dates = sorted(pool)
    out: Dict[str, Dict[str, Any]] = {}
    for i, date in enumerate(dates):
        if i == 0:
            continue                       # 第一天没有「昨日」，算不出晋级
        prev = dates[i - 1]
        row: Dict[str, Any] = {}
        for n in PROMOTION_LEVELS:
            base_set = pool[prev].get(n) or set()
            up_set = pool[date].get(n + 1) or set()
            hit = base_set & up_set
            row["promotion_%db" % n] = (len(hit) / len(base_set)) if base_set else None
            row["base_%db" % n] = len(base_set)
            row["hit_%db" % n] = len(hit)
        row["prev_date"] = prev
        out[date] = row
    return out


def load_promotions(conn) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """合并晋级率：库内原值优先，缺失用本地复算。

    返回 ({date: {"rate": 比例, "base": 分母样本数|None, "source": str}}, meta)。
    · `sentiment.promotion_*` 存的是**百分数**（40.0 = 40%），引擎用**比例**（0.40），此处换算；
    · 库内原值没有分母样本数，此时优先取本地涨停池同日算出的分母（供小样本保护用），
      两处都没有则 `base=None`（引擎按原口径参与，并在 note 里注明无样本数）。
    """
    pool, pool_source = load_pool()
    derived = derive_promotions(pool)

    db_rows = [dict(r) for r in conn.execute(
        "SELECT trade_date, promotion_2b FROM sentiment WHERE ts='close'")]
    db_rates = {str(r["trade_date"]): r["promotion_2b"] for r in db_rows if r["promotion_2b"] is not None}

    samples: Dict[str, Dict[str, Any]] = {}
    for date, row in derived.items():
        rate = row.get("promotion_2b")
        if rate is None:
            continue
        samples[date] = {
            "rate": float(rate),
            "base": row.get("base_2b"),
            "source": pool_source.get(date, "limitup"),
        }
    for date, pct in db_rates.items():
        try:
            rate = float(pct) / 100.0
        except (TypeError, ValueError):
            continue
        base = samples.get(date, {}).get("base")
        samples[date] = {"rate": rate, "base": base, "source": "db"}

    available = sorted(samples)
    origin = {d: samples[d]["source"] for d in available}
    meta = {
        "factor_available": bool(available),
        "n_dates": len(available),
        "first_date": available[0] if available else None,
        "last_date": available[-1] if available else None,
        "origin": origin,
        "min_sample": MIN_PROMOTION_SAMPLE,
        "pool_dates": len(pool),
        "pool_first": min(pool) if pool else None,
        "pool_last": max(pool) if pool else None,
        "premium_available": False,
        "premium_reason": "本地无昨日涨停股今日涨幅的全A快照；premium 字段仍为 None，不参与打分",
        "caliber": "晋级率_N = |昨日N板 ∩ 今日N+1板| / |昨日N板|（本地涨停池复算；库内已有值优先）；"
                   "分母 < %d 只不给分" % MIN_PROMOTION_SAMPLE,
    }
    return samples, meta


# ---------------------------------------------------------------- 交叉校验

def sector_ladder_totals() -> Dict[str, float]:
    """{YYYYMMDD: limitup_total}（`sector_ladder_*.json`），供与 zt_total 逐日比对。"""
    base = panel_dir()
    sig = _dir_signature(base, SECTOR_LADDER_RE)
    cached = _FILE_CACHE.get("ladder_totals")
    if cached and cached[0] == sig and (time.time() - cached[1]) < CACHE_TTL_S:
        return cached[2]

    out: Dict[str, float] = {}
    if base.is_dir():
        for name in os.listdir(base):
            m = SECTOR_LADDER_RE.search(name)
            if not m:
                continue
            payload = _read_json(base / name)
            if isinstance(payload, dict) and payload.get("limitup_total") is not None:
                try:
                    out[m.group(1)] = float(payload["limitup_total"])
                except (TypeError, ValueError):
                    continue
    _FILE_CACHE["ladder_totals"] = (sig, time.time(), out)
    return out


# ---------------------------------------------------------------- 组装

def build_payload(conn, history_days: int = 60) -> Dict[str, Any]:
    """面板要的全部数据：今日明细 + 近 N 日序列 + 口径元信息。

    `build.py`（离线证据导出）与 `backend.py`（在线）共用本函数，保证「页面看到的」与
    「落盘证据」是同一份计算，不是两套实现。
    """
    rows = load_sentiment(conn)
    promotions, promo_meta = load_promotions(conn)
    series = engine.score_series(rows, promotions)

    by_date = {r["trade_date"]: r for r in rows}
    for point in series:
        row = by_date.get(point["date"]) or {}
        point["stage"] = row.get("stage")
        point["stage_note"] = row.get("stage_note")
        point["zt_total"] = row.get("zt_total")
        point["zt_ever"] = row.get("zt_ever")
        point["dt_total"] = row.get("dt_total")
        point["height"] = row.get("height")
        point["lb2plus"] = row.get("lb2plus")
        point["up_cnt"] = row.get("up_cnt")
        point["down_cnt"] = row.get("down_cnt")
        point["reseal_rate"] = row.get("reseal_rate")
        sample = promotions.get(point["date"]) or {}
        point["promotion_2b"] = sample.get("rate")
        point["promotion_base"] = sample.get("base")
        point["promotion_source"] = sample.get("source")

    today = series[-1] if series else None

    # 交叉校验：板块涨停梯队的 limitup_total vs sentiment.zt_total
    totals = sector_ladder_totals()
    diffs = []
    for point in series:
        ref = totals.get(point["date"])
        if ref is None or point.get("zt_total") is None:
            continue
        if abs(ref - float(point["zt_total"])) > 0.5:
            diffs.append({"date": point["date"], "sector_ladder": ref, "sentiment": point["zt_total"]})

    window = series[-history_days:] if history_days and history_days > 0 else series
    return {
        "ok": True,
        "asof": series[-1]["date"] if series else None,
        "today": today,
        "history": window,
        "history_full_n": len(series),
        "n_trade_dates": len(rows),
        "window": engine.WINDOW,
        "base_score": engine.BASE_SCORE,
        "bands": [{"lower": lo, "label": label} for lo, label in engine.BANDS],
        "burst_sign": engine.BURST_SIGN,
        "min_promotion_sample": engine.MIN_PROMOTION_SAMPLE,
        "caliber_version": engine.CALIBER_VERSION,
        "caliber_revised": engine.CALIBER_REVISED,
        "caliber_revision_note": engine.CALIBER_REVISION_NOTE,
        "promotion": {k: v for k, v in promo_meta.items() if k != "origin"},
        "checks": {
            "sector_ladder_files": len(totals),
            "zt_total_mismatch": diffs[:10],
            "zt_total_mismatch_n": len(diffs),
        },
        "caliber": {
            "version": engine.CALIBER_VERSION,
            "revised": engine.CALIBER_REVISED,
            "revision_note": engine.CALIBER_REVISION_NOTE,
            "source": "track_pool.db::sentiment (ts=close) + xiaoshi_cache 涨停池",
            "stage": "stage/stage_note 是规则硬判，与打分并列展示、不合并",
            "window_includes_today": True,
            "burst_formula": "clamp(20 − 炸板率×100, ±20)",
            "promotion_formula": "clamp((晋级率 − 0.15) × 100, ±15)；分母 < %d 只不给分" % engine.MIN_PROMOTION_SAMPLE,
        },
    }


def clear_cache() -> None:
    """测试与离线脚本用：丢掉进程内文件缓存。"""
    _FILE_CACHE.clear()
