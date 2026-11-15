# -*- coding: utf-8 -*-
"""板块层缓存与取数口径的守门测试（2026-10-25 性能整改）。

背景：主人报「8810 切层级明显比 8800/8002 卡」。实测三处根因：
  1. `/board_flow` 每次请求全量重算（L3 层 1.45s，其中 `_dual_bull` 跑 231 板块 × 500 日状态机）；
  2. `/rank_recent` 每次重查（L3 层 390ms、287KB）；
  3. 前端取 250 天，8002 只取 80 天（数据量差 3 倍）。
这里把「必须有缓存」和「天数必须与 8002 同口径」钉在源码上——
没有断言时，下次重构很容易把缓存删掉而无人察觉（性能退化不会报错，只会变慢）。
"""
from __future__ import annotations

import re
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]      # app/plugins/sector
BACKEND = PLUGIN / "backend.py"
FRONTEND = PLUGIN / "frontend.js"


def _backend() -> str:
    return BACKEND.read_text(encoding="utf-8")


def _frontend() -> str:
    return FRONTEND.read_text(encoding="utf-8")


def test_flow_cache_exists_and_keyed_by_date() -> None:
    """`/board_flow` 必须有进程内缓存，且键含最新交易日（ds 前进即自动失效）。"""
    src = _backend()
    assert "_FLOW_CACHE" in src, "board_flow 缺缓存：L3 层每次重算 1.45s"
    # 键必须是 (标签, ds, period, level, 可选库戳) —— 少 ds 会把昨天的结果发给今天；
    # 2026-11-07 加库戳：16:10–16:21 链重写库时的半成品不得整晚复用。
    assert re.search(r"ck\s*=\s*\(\s*\"bf\"\s*,\s*ds\s*,\s*period\s*,\s*level[^)]*\)", src), \
        "board_flow 缓存键缺 ds/period/level"
    # 命中必须早于计算（否则缓存白加）
    hit_at = src.index("hit = _FLOW_CACHE.get(ck)")
    calc_at = src.index("fl = _board_flow(conn, ds, period, level, None)")
    assert hit_at < calc_at, "缓存命中判断必须在重算之前"


def test_rank_recent_cache_exists() -> None:
    """`/rank_recent` 同样要缓存（L3 层 390ms / 287KB）。"""
    src = _backend()
    assert "_RECENT_CACHE" in src, "rank_recent 缺缓存"
    assert re.search(r"ck\s*=\s*\(\s*\"rr\"\s*,\s*ds\s*,\s*level\s*,\s*days[^)]*\)", src), \
        "rank_recent 缓存键缺 ds/level/days"


def test_cache_has_size_cap() -> None:
    """缓存必须有上限：无界缓存会随层级×周期×日期膨胀。"""
    src = _backend()
    assert "_CACHE_MAX" in src
    assert src.count(">= _CACHE_MAX") >= 2, "两类缓存都要有上限判断"


def test_cache_not_used_for_writes() -> None:
    """缓存只挂在两个只读构建函数里，不得出现在写类端点。

    判据：取源码里的**函数块**，看哪些块提到缓存变量 —— 只允许是
    `build_board_flow` / `build_rank_recent`（含模块顶部声明区）。
    """
    src = _backend()
    # 按顶层 def 切块（顶格 def 才是模块级函数）
    parts = re.split(r"\n(?=def )", src)
    allowed = {"build_board_flow", "build_rank_recent"}
    offenders = []
    for chunk in parts:
        m = re.match(r"def ([A-Za-z_]+)\(", chunk)
        name = m.group(1) if m else None
        if name is None:
            continue                      # 模块顶部声明区，允许
        if ("_FLOW_CACHE" in chunk or "_RECENT_CACHE" in chunk) and name not in allowed:
            offenders.append(name)
    assert not offenders, "缓存出现在非只读函数里：%s" % offenders
    # 反向确认：这两个函数确实用了缓存（防拼写漂移导致上面空过）
    flow_chunk = next(c for c in parts if c.startswith("def build_board_flow("))
    rr_chunk = next(c for c in parts if c.startswith("def build_rank_recent("))
    assert "_FLOW_CACHE" in flow_chunk
    assert "_RECENT_CACHE" in rr_chunk


def test_frontend_uses_80_days_like_8002() -> None:
    """前端取数天数必须与 8002 同口径（80 日）。

    8002 manifest: n_days=80 / show_start=20。取 250 日会让 L3 返回 287KB（8002 只有 92KB）。
    80 日仍覆盖：双滑块区间、20 天周期、在榜 30 日窗口。
    """
    src = _frontend()
    assert re.search(r"const RECENT_DAYS = 80;", src), "取数天数不是 80"
    assert "days=' + RECENT_DAYS" in src, "rank_recent 请求没用 RECENT_DAYS"
    assert "days=250" not in src, "仍存在写死的 250 天请求"


def test_frontend_caches_levels_and_flow() -> None:
    """卡内要缓存层级数据与 flow，切回时不重复请求（8002 就是这样才快）。"""
    src = _frontend()
    assert "_rrCache" in src, "缺层级数据缓存"
    assert "_flowCache" in src, "缺 flow 缓存"
    # 空 flow 不入缓存：后端刚重启/当日无数据时不能把空结果钉死
    assert re.search(r"if \(map && Object\.keys\(map\)\.length\)", src), \
        "空 flow 结果不应入缓存"
