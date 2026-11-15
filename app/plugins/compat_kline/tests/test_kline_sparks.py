# -*- coding: utf-8 -*-
"""/api/kline_sparks 契约测试（2026-11-04 自选池打开慢）。

背景：自选池打开慢（实测 tPaint 中位 1491 ms / 最差 3094 ms）。
取证（37 票当前组）：前端逐票拉 `/api/kline?period=d&limit=160`，服务端票均 112.8 ms，
其中 build_mf 43.8 / _amv_map 31.9 / _boards 4.9 / _float_shares 3.9 —— 自选卡副图一个都不用。
新端点：批量取数 + 只回末 45 根的 6 条序列 + 20 s 缓存。

本文件守两件事：
  ① 口径与 /api/kline **逐值相等**（换端点不能把图画变形）；
  ② 批量与缓存真的生效（不是把慢的搬个位置）。
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient  # noqa: F401  （夹具同款，保持依赖一致）

HERE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[4]
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.plugins.compat_kline import backend  # noqa: E402

from test_compat_kline import (  # noqa: E402
    _FakeQuotes,
    _client,
    _dbars_span,
    _mk_ctx,
)


def _amv_rows(n: int, code6: str = "600519", base: float = 1.2e9):
    d0 = date(2025, 9, 1)
    return [((d0 + timedelta(days=i)).isoformat(), code6, base + i * 1e7) for i in range(n)]


def _reset() -> None:
    backend._sparks_cache.clear()


# ---------------------------------------------------------------- 口径一致性


def test_sparks_matches_kline_series(tmp_path):
    """c/y/w/a/m/aw 与 /api/kline 同票同根逐值相等（黄白线与 AMV 三线同实现）。"""
    _reset()
    n = 260
    quotes = _FakeQuotes(daily=_dbars_span(n))
    ctx = _mk_ctx(tmp_path, quotes=quotes, amv_rows=_amv_rows(n))
    cli = _client(ctx)
    k = cli.get("/api/kline/600519?period=d&limit=160").json()
    sp = cli.get("/api/kline_sparks?codes=600519&tail=45&limit=160").json()
    s = sp["items"]["600519"]
    tail = k["bars"][-45:]
    assert s["t"] == tail[-1]["t"][:8]
    assert len(s["c"]) == 45 and len(s["a"]) == 45 and len(s["aw"]) == 45
    for i in range(45):
        assert s["c"][i] == round(float(tail[i]["c"]), 3), (i, s["c"][i], tail[i]["c"])
        assert s["y"][i] == tail[i]["yellow"], (i, s["y"][i], tail[i]["yellow"])
        assert s["w"][i] == tail[i]["white"], (i, s["w"][i], tail[i]["white"])
        assert s["a"][i] == tail[i]["amv"], (i, s["a"][i], tail[i]["amv"])
        assert s["m"][i] == tail[i]["amvMa"], (i, s["m"][i], tail[i]["amvMa"])
        assert s["aw"][i] == tail[i]["amvWhite"], (i, s["aw"][i], tail[i]["amvWhite"])


def test_sparks_short_history_still_has_white(tmp_path):
    """历史不足 limit 根（次新股）：白线必须有值，不能整段置空（与 _kline_payload 兜底同口径）。"""
    _reset()
    n = 40
    quotes = _FakeQuotes(daily=_dbars_span(n))
    ctx = _mk_ctx(tmp_path, quotes=quotes, amv_rows=_amv_rows(n))
    cli = _client(ctx)
    k = cli.get("/api/kline/600519?period=d&limit=160").json()
    sp = cli.get("/api/kline_sparks?codes=600519&tail=45&limit=160").json()
    s = sp["items"]["600519"]
    assert len(s["c"]) == n
    assert s["w"] == [b["white"] for b in k["bars"]], "白线与 /api/kline 不一致"
    assert all(v is not None for v in s["w"])


# ---------------------------------------------------------------- 载荷与批量


def test_sparks_omits_heavy_extras(tmp_path):
    """不返回 mf / boards / turnover —— 那几项占 /api/kline 84% 耗时且自选卡不用。"""
    _reset()
    quotes = _FakeQuotes(daily=_dbars_span(140))
    ctx = _mk_ctx(tmp_path, quotes=quotes, amv_rows=_amv_rows(140))
    sp = _client(ctx).get("/api/kline_sparks?codes=600519&tail=45&limit=160").json()
    s = sp["items"]["600519"]
    assert set(s) == {"t", "c", "y", "w", "a", "m", "aw"}, sorted(s)
    for gone in ("mf", "boards", "prev_close", "turnover"):
        assert gone not in sp, gone


def test_sparks_multi_codes_one_response(tmp_path):
    """多票一次请求：一票一组序列，不是每票一次请求。"""
    _reset()
    n = 140
    rows = []
    for c6 in ("600519", "000001", "300750"):
        rows += _amv_rows(n, c6, 1.0e9)
    quotes = _FakeQuotes(daily=_dbars_span(n))
    ctx = _mk_ctx(tmp_path, quotes=quotes, amv_rows=rows)
    sp = _client(ctx).get(
        "/api/kline_sparks?codes=600519,000001,300750&tail=45&limit=160").json()
    assert sp["n"] == 3
    for c in ("600519", "000001", "300750"):
        assert len(sp["items"][c]["c"]) == 45


# ---------------------------------------------------------------- 缓存与降级


def test_sparks_cache_avoids_refetch(tmp_path):
    """20 s TTL：同一批码短时间内重开不再取数（自选池 5 s 轮询不打穿）。"""
    _reset()
    quotes = _FakeQuotes(daily=_dbars_span(140))
    ctx = _mk_ctx(tmp_path, quotes=quotes, amv_rows=_amv_rows(140))
    cli = _client(ctx)
    cli.get("/api/kline_sparks?codes=600519&tail=45&limit=160")
    n1 = len([c for c in quotes._calls if c[0] == "bars.get"])
    cli.get("/api/kline_sparks?codes=600519&tail=45&limit=160")
    n2 = len([c for c in quotes._calls if c[0] == "bars.get"])
    assert n1 > 0 and n2 == n1, (n1, n2)


def test_sparks_empty_codes_is_400(tmp_path):
    _reset()
    ctx = _mk_ctx(tmp_path, quotes=_FakeQuotes(daily=_dbars_span(10)))
    cli = _client(ctx)
    assert cli.get("/api/kline_sparks?codes=").status_code == 400
    assert cli.get("/api/kline_sparks").status_code == 400


def test_sparks_skips_bad_code(tmp_path):
    """非法码跳过、其余照常返回：一张坏票不能拖垮整组。"""
    _reset()
    quotes = _FakeQuotes(daily=_dbars_span(140))
    ctx = _mk_ctx(tmp_path, quotes=quotes, amv_rows=_amv_rows(140))
    sp = _client(ctx).get(
        "/api/kline_sparks?codes=NOTACODE,600519&tail=45&limit=160").json()
    assert "600519" in sp["items"]


def test_sparks_amv_missing_no_fabrication(tmp_path):
    """AMV 库缺失：副图三线给 null，不拿别的数顶替。"""
    _reset()
    quotes = _FakeQuotes(daily=_dbars_span(140))
    ctx = _mk_ctx(tmp_path, quotes=quotes)          # 不建 amv_board.db
    sp = _client(ctx).get("/api/kline_sparks?codes=600519&tail=45&limit=160").json()
    s = sp["items"]["600519"]
    assert len(s["c"]) == 45
    assert all(v is None for v in s["a"]), "AMV 缺失时副图应全 null，不编造"
