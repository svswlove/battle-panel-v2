# -*- coding: utf-8 -*-
"""compat_kline 板块/概念指数 K 线（`*.TI`）迁移测试（2026-10-24 S2）。

覆盖（对应任务书 §4 测试清单）：
  1. 日线取数（ths_index.db::index_daily，升序、量纲、换手/mf 恒 None、index/name）
  2. 周线聚合（ISO 自然周 o/h/l/c/v/amount 口径、t 取当周最后一根）
  3. before 游标（严格早于、has_more/next_before 只在 before 分支出现）
  4. 空库降级（无库/无该码 → 200 + 空 bars + stale=True，不 500、不编造）
  5. 分钟 400（板块无逐笔源 → 明确报错）
  6. 周线源不可用降级（period_unavailable + unavailable_reason，不拿日线聚合冒充）
  7. stale 口径（应收到的最后一根 vs 库里最新一根）
  8. AMV 副图（sector_daily，元 → 亿元；date 有横线、bar 的 t 无横线，坑位回归）

全部用例**零网络、零生产库**：tmp_path 建 sqlite 替身，`ctx.quotes` 用 `_FakeQuotes`。
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.contract import validate_manifest  # noqa: E402
from app.plugins.compat_kline import backend  # noqa: E402
from app.plugins.compat_kline.tests.test_compat_kline import (  # noqa: E402
    OLD_KLINE_TOP,
    OLD_KLINE_TOP_PAGED,
    _client,
    _FakeQuotes,
    _mk_ctx,
)

PLUGIN_DIR = Path(__file__).resolve().parents[1]

INDEX_CODE = "881121.TI"

# 旧 8800 `build_index_kline_payload` 的顶层键 = 个股顶层 + index/name
# （日线；`mf` 键存在但恒 null）
OLD_INDEX_TOP = OLD_KLINE_TOP | {"index", "name"}
# 周线源不可用时额外两个标记键
OLD_INDEX_TOP_WEEK_UNAVAIL = OLD_INDEX_TOP | {"period_unavailable", "unavailable_reason"}


# ---------------------------------------------------------------- 造数


def _day(i: int, *, month: int = 10, base: float = 1000.0) -> str:
    return "2026%02d%02d" % (month, i)


def _idx_rows(days: List[str], *, base: float = 1000.0, vol: float = 1e8,
              amount: float = 1e10) -> List[tuple]:
    """`[(date8, o, h, l, c, volume, turnover)]`，收盘逐日 +1（便于断言 chg/amp）。"""
    out = []
    for k, d in enumerate(days):
        c = base + k
        out.append((d, c - 1.0, c + 5.0, c - 5.0, c, vol + k, amount + k * 1000))
    return out


def _client_for(tmp_path: Path, **kw) -> TestClient:
    return _client(_mk_ctx(tmp_path, quotes=kw.pop("quotes", None) or _FakeQuotes(), **kw))


def _sub(tmp_path: Path, name: str) -> Path:
    """子目录（同一用例要建两套库时用；`_mk_ctx` 要求目录已存在）。"""
    d = tmp_path / name
    d.mkdir()
    return d


@pytest.fixture(autouse=True)
def _clear_week_memo():
    """周线 60s 记忆是模块级全局 → 每个用例前后清空，避免跨用例污染（顺序无关）。"""
    backend._THS_WEEK_MEMO.clear()
    yield
    backend._THS_WEEK_MEMO.clear()


# ---------------------------------------------------------------- 1. 日线取数


def test_index_daily_shape_and_scale(tmp_path):
    """日线：顶层键/bar 键与旧端同构；量纲 v=股、amount=元；换手/mf/个股AMV 恒 None。"""
    rows = _idx_rows([_day(9), _day(10), _day(11)])
    c = _client_for(tmp_path, index_rows=rows)
    body = c.get("/api/kline/%s?period=d&limit=120" % INDEX_CODE).json()

    assert set(body) == OLD_INDEX_TOP, set(body) ^ OLD_INDEX_TOP
    assert body["index"] is True
    assert body["name"] == "白酒"                 # ths_board.db::board 命中
    assert body["code"] == INDEX_CODE
    assert body["period"] == "d"
    assert body["source"] == "ths_index.db"
    assert body["vol_unit"] == "股"
    assert body["adj"] == "front"
    assert body["count"] == 3
    assert body["asof"] == _day(11)
    # 板块无该三口径 → 恒 null（不是缺键）
    assert body["mf"] is None
    assert body["boards"] == {"industry": [], "concepts": []}
    assert [b["turnover"] for b in body["bars"]] == [None, None, None]

    bar = body["bars"][-1]
    assert set(bar) == {
        "t", "o", "h", "l", "c", "v", "yellow", "white", "amv", "amvMa", "amvWhite",
        "chg", "amp", "turnover", "amount",
    }, set(bar)
    # 量纲：volume=股 原值直出、turnover=元 进 amount（不 ×100、不 /1e8）
    assert bar["v"] == rows[-1][5]
    assert bar["amount"] == rows[-1][6]
    # 首根无前收 → chg/amp 为 None；第二根起有值
    assert body["bars"][0]["chg"] is None and body["bars"][0]["amp"] is None
    assert bar["chg"] == round((rows[-1][4] / rows[-2][4] - 1) * 100, 2)
    assert bar["amp"] == round((rows[-1][2] - rows[-1][3]) / rows[-2][4] * 100, 2)
    assert body["prev_close"] == round(rows[-2][4], 3)


def test_index_daily_limit_clamped_to_old_range(tmp_path):
    """limit 夹到 [120, 800]（旧端同：limit=1 → 120，limit=99999 → 800）。"""
    rows = _idx_rows([_day(i) for i in range(1, 6)])
    c = _client_for(tmp_path, index_rows=rows)
    # 库里只有 5 根 → 全给（不补、不编造），但入参已夹到 120
    assert c.get("/api/kline/%s?limit=1" % INDEX_CODE).json()["count"] == 5
    assert backend.KLINE_LIMIT_MIN == 120 and backend.KLINE_LIMIT_MAX == 800
    # 900 根 → 只回 800（上限生效）
    days = []
    d = date(2022, 1, 3)
    while len(days) < 900:
        if d.weekday() < 5:
            days.append(d.strftime("%Y%m%d"))
        d = date.fromordinal(d.toordinal() + 1)
    many = _idx_rows(days)
    c2 = _client_for(_sub(tmp_path, "b"), index_rows=many)
    body = c2.get("/api/kline/%s?limit=99999" % INDEX_CODE).json()
    assert body["count"] == 800
    assert body["asof"] == days[-1]
    assert body["bars"][0]["t"] == days[-800]


def test_index_daily_name_falls_back_to_sector_meta(tmp_path):
    """板块名：ths_board 无该码 → amv_board::sector_meta；都没有 → 空串（不编造）。"""
    rows = _idx_rows([_day(9)])
    c = _client_for(tmp_path, index_rows=rows, sector_rows=[],
                    board=False, sector_name="半导体")
    assert c.get("/api/kline/%s" % INDEX_CODE).json()["name"] == "半导体"
    c2 = _client_for(_sub(tmp_path, "b"), index_rows=rows, board=False)
    assert c2.get("/api/kline/%s" % INDEX_CODE).json()["name"] == ""


# ---------------------------------------------------------------- 2. 周线聚合


def test_index_week_aggregation_iso_weeks(tmp_path):
    """周线：ISO 自然周聚合 o=首/h=max/l=min/c=末/v+amount=求和，t=当周最后一根。"""
    # 2026-09-28(一) ~ 2026-10-09(五)：跨两周（ISO 40 周、41 周）
    days = ["20260928", "20260929", "20260930", "20261001", "20261002",
            "20261005", "20261006", "20261007", "20261008", "20261009"]
    rows = _idx_rows(days)
    ctx = _mk_ctx(tmp_path, quotes=_FakeQuotes(), index_rows=rows)
    bars = backend._index_kline_rows(ctx, INDEX_CODE, "w", 800)
    assert len(bars) == 2, bars
    w1, w2 = bars
    assert w1["t"] == "20261002"          # 当周最后一根（周五）
    assert w2["t"] == "20261009"
    # 第一周：首=0928 开，高=max，低=min，收=1002 收，量/额求和
    src1 = [r for r in rows if r[0] <= "20261002"]
    assert w1["o"] == src1[0][1]
    assert w1["h"] == max(r[2] for r in src1)
    assert w1["l"] == min(r[3] for r in src1)
    assert w1["c"] == src1[-1][4]
    assert w1["v"] == sum(r[5] for r in src1)
    assert w1["amount"] == sum(r[6] for r in src1)


def test_index_week_iso_year_boundary(tmp_path):
    """跨年周按 ISO 年归属：2026-12-31(四) 与 2027-01-01(五) 同属 ISO 2026-W53。"""
    rows = _idx_rows(["20261230", "20261231", "20270101"])
    ctx = _mk_ctx(tmp_path, quotes=_FakeQuotes(), index_rows=rows)
    bars = backend._index_kline_rows(ctx, INDEX_CODE, "w", 800)
    assert len(bars) == 1, bars
    assert bars[0]["t"] == "20270101"
    assert bars[0]["c"] == rows[-1][4]


def test_index_week_null_volume_treated_as_zero(tmp_path):
    """库里 volume/turnover 为 NULL 的行（自建指数首根）不得抛，按 0 累加。"""
    rows = [("20261005", 1.0, 2.0, 0.5, 1.5, None, None),
            ("20261006", 1.5, 2.5, 1.0, 2.0, 100.0, 1000.0)]
    ctx = _mk_ctx(tmp_path, quotes=_FakeQuotes(), index_rows=rows)
    bars = backend._index_kline_rows(ctx, INDEX_CODE, "w", 800)
    assert len(bars) == 1
    assert bars[0]["v"] == 100.0 and bars[0]["amount"] == 1000.0
    # 日线也不得抛（旧端 500 缺陷的回归钉）
    daily = backend._index_kline_rows(ctx, INDEX_CODE, "d", 800)
    assert len(daily) == 2 and daily[0]["v"] is None


def test_index_week_uses_native_source_not_aggregation(tmp_path):
    """周线**只认原生 1w**：门面有接缝就用它，绝不拿日线聚合冒充。"""
    rows = _idx_rows([_day(9), _day(10), _day(11)])
    native = [{"t": "20261009", "o": 900.0, "h": 1100.0, "l": 800.0, "c": 1000.0,
               "v": 5e9, "amount": 5e10}]
    q = _FakeQuotes()
    q.ths_index_weekly = lambda code, s, e: native
    c = _client_for(tmp_path, quotes=q, index_rows=rows)
    body = c.get("/api/kline/%s?period=w" % INDEX_CODE).json()
    assert body["source"] == "ths_mcp_index:1w"
    assert body["count"] == 1
    assert body["bars"][0]["t"] == "20261009"
    assert body["bars"][0]["v"] == 5e9
    assert "period_unavailable" not in body


def test_index_week_falls_back_to_daily_aggregation(tmp_path):
    """周线源不可用（2026-11-01 实测上游 1w/1mo 恒定 5003）→ 用本库日线按 ISO 周聚合。

    与旧测试的差别：不再报 period_unavailable —— 上游确实没周线，但本库有日线，
    同一数据源重采样出周线是可用的（source 与 week_from_daily 都如实标出）。
    真没日线时仍然报不可用（见 test_index_week_unavailable_when_no_daily）。
    """
    rows = _idx_rows([_day(9), _day(10), _day(11)])
    c = _client_for(tmp_path, index_rows=rows)      # 门面**没有** ths_index_weekly 接缝
    body = c.get("/api/kline/%s?period=w&limit=120" % INDEX_CODE).json()

    assert body["source"] == "ths_index.db:1w(日线聚合)", body["source"]
    assert body["week_from_daily"] is True
    assert "period_unavailable" not in body and "unavailable_reason" not in body
    assert body["count"] > 0 and body["bars"][-1]["c"] == 1002.0
    assert body["index"] is True and body["name"] == "白酒"
    # 默认窗口不带分页键（与个股同）
    assert "has_more" not in body and "next_before" not in body


def test_index_week_unavailable_when_no_daily(tmp_path):
    """周线源不可用且本地无日线 → 仍然空 bars + period_unavailable（不编造）。"""
    c = _client_for(tmp_path, index_rows=[])
    body = c.get("/api/kline/%s?period=w&limit=120" % INDEX_CODE).json()
    assert set(body) == OLD_INDEX_TOP_WEEK_UNAVAIL, set(body) ^ OLD_INDEX_TOP_WEEK_UNAVAIL
    assert body["bars"] == [] and body["count"] == 0
    assert body["stale"] is True
    assert body["source"] == "ths_mcp_index:unavailable"
    assert body["period_unavailable"] is True
    assert body["asof"] is None and body["prev_close"] is None


def test_index_week_daily_fallback_with_before_carries_paging_keys(tmp_path):
    c = _client_for(tmp_path, index_rows=_idx_rows([_day(9), _day(10)]))
    body = c.get("/api/kline/%s?period=w&before=20261016" % INDEX_CODE).json()
    assert body["source"] == "ths_index.db:1w(日线聚合)"
    assert "has_more" in body and "next_before" in body
    assert body["week_from_daily"] is True


def test_index_week_memo_60s(tmp_path, monkeypatch):
    """同码 60s 内复用记忆，不重复打上游；超过 TTL 再取一次。"""
    calls: List[tuple] = []
    q = _FakeQuotes()
    q.ths_index_weekly = lambda code, s, e: (calls.append((code, s, e)) or
                                             [{"t": "20261009", "o": 1, "h": 2, "l": 0.5,
                                               "c": 1.5, "v": 10.0, "amount": 100.0}])
    ctx = _mk_ctx(tmp_path, quotes=q, index_rows=[])
    backend._THS_WEEK_MEMO.clear()
    backend.build_index_kline(ctx, INDEX_CODE, period="w")
    backend.build_index_kline(ctx, INDEX_CODE, period="w")
    assert len(calls) == 1, calls
    # 把记忆时间戳往前推 61s → 过期后重新取
    ts, bars = backend._THS_WEEK_MEMO[INDEX_CODE]
    backend._THS_WEEK_MEMO[INDEX_CODE] = (ts - 61.0, bars)
    backend.build_index_kline(ctx, INDEX_CODE, period="w")
    assert len(calls) == 2, calls
    backend._THS_WEEK_MEMO.clear()


def test_index_week_upstream_exception_degrades(tmp_path):
    """上游抛异常 → 视为不可用（不抛穿、不 500）。"""
    q = _FakeQuotes()

    def boom(code, s, e):
        raise RuntimeError("upstream down")

    q.ths_index_weekly = boom
    backend._THS_WEEK_MEMO.clear()
    c = _client_for(tmp_path, quotes=q, index_rows=[])
    r = c.get("/api/kline/%s?period=w" % INDEX_CODE)
    assert r.status_code == 200
    assert r.json()["period_unavailable"] is True
    backend._THS_WEEK_MEMO.clear()


# ---------------------------------------------------------------- 3. before 游标


def test_index_before_cursor_strictly_earlier(tmp_path):
    """before=YYYYMMDD 严格早于游标（不含当日），并带 has_more / next_before。"""
    rows = _idx_rows([_day(i) for i in range(1, 11)])
    c = _client_for(tmp_path, index_rows=rows)
    body = c.get("/api/kline/%s?period=d&limit=120&before=%s" % (INDEX_CODE, _day(6))).json()
    assert set(body) == OLD_KLINE_TOP_PAGED | {"index", "name"}, set(body)
    assert [b["t"] for b in body["bars"]] == [_day(i) for i in range(1, 6)]
    assert body["has_more"] is False
    assert body["next_before"] == _day(1)


def test_index_before_has_more_when_more_rows(tmp_path):
    """窗口内根数 > limit → has_more=True 且只回最后 limit 根（多取 1 根判边界）。"""
    days = []
    for m in range(1, 13):
        for d in range(1, 21):
            days.append("2025%02d%02d" % (m, d))
    rows = _idx_rows(days)          # 240 根
    c = _client_for(tmp_path, index_rows=rows)
    # 全部早于最后一天 → 239 根 > 120 → has_more=True，只回最后 120 根
    body = c.get("/api/kline/%s?period=d&limit=120&before=%s"
                 % (INDEX_CODE, days[-1])).json()
    assert body["count"] == 120 and body["has_more"] is True
    assert body["asof"] == days[-2]
    assert body["next_before"] == body["bars"][0]["t"]
    # 窗口内只有 119 根 → has_more=False
    body2 = c.get("/api/kline/%s?period=d&limit=120&before=%s"
                  % (INDEX_CODE, days[119])).json()
    assert body2["count"] == 119 and body2["has_more"] is False


def test_index_before_dashed_string_is_not_normalized(tmp_path):
    """`before=2026-06-01`（带横线）**不归一**——旧端原值进 SQL，字典序只截到上一年度。

    这是旧端真实行为（双跑对拍要求逐字段一致），不是本实现的取舍；
    个股分支才有剥非数字的归一（`_kline_validate` 那一路）。
    """
    days = ["20251231", "20260105", "20260601", "20260602"]
    c = _client_for(tmp_path, index_rows=_idx_rows(days))
    body = c.get("/api/kline/%s?period=d&before=2026-06-01" % INDEX_CODE).json()
    # 字典序：'2026-06-01' 第 5 字符 '-' (0x2D) < '0' (0x30) ⇒ 只有上一年度的行命中
    assert [b["t"] for b in body["bars"]] == ["20251231"], body["bars"]
    assert body["has_more"] is False


def test_index_before_earlier_than_history_gives_empty_but_200(tmp_path):
    rows = _idx_rows([_day(i) for i in range(1, 6)])
    c = _client_for(tmp_path, index_rows=rows)
    r = c.get("/api/kline/%s?period=d&before=20200101" % INDEX_CODE)
    assert r.status_code == 200
    body = r.json()
    assert body["bars"] == [] and body["count"] == 0
    assert body["has_more"] is False and body["next_before"] is None
    assert body["stale"] is True


# ---------------------------------------------------------------- 4. 空库降级


def test_index_missing_db_degrades_to_empty(tmp_path):
    """无库（ctx.db_read 抛 FileNotFound）→ 200 + 空 bars + stale，不 500。"""
    c = _client_for(tmp_path, index_rows=None)      # 不建 ths_index.db
    r = c.get("/api/kline/%s?period=d" % INDEX_CODE)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == OLD_INDEX_TOP
    assert body["bars"] == [] and body["count"] == 0
    assert body["stale"] is True and body["source"] == "ths_index.db"
    assert body["asof"] is None and body["prev_close"] is None


def test_index_unknown_code_degrades_to_empty(tmp_path):
    """库里无该码 → 空 bars + stale + name 空串（旧端 999999.TI 实测同形）。"""
    c = _client_for(tmp_path, index_rows=_idx_rows([_day(9)]), board=False)
    body = c.get("/api/kline/999999.TI?period=d").json()
    assert body["count"] == 0 and body["stale"] is True and body["name"] == ""


def test_index_ignores_null_o_rows(tmp_path):
    """`o IS NOT NULL` 过滤与旧端同（脏行不进 K 线）。"""
    rows = [(_day(9), None, 2.0, 0.5, 1.5, 10.0, 100.0),
            (_day(10), 1.5, 2.5, 1.0, 2.0, 20.0, 200.0)]
    c = _client_for(tmp_path, index_rows=rows)
    body = c.get("/api/kline/%s?period=d" % INDEX_CODE).json()
    assert [b["t"] for b in body["bars"]] == [_day(10)]


# ---------------------------------------------------------------- 5. 分钟 400


def test_index_minute_periods_400(tmp_path):
    c = _client_for(tmp_path, index_rows=[])
    for p in ("m1", "m5", "m15", "m30", "m60"):
        r = c.get("/api/kline/%s?period=%s" % (INDEX_CODE, p))
        assert r.status_code == 400, p
        assert "板块指数暂无 %s 周期数据" % p in r.json()["detail"], r.json()


def test_index_intraday_endpoints_reject_ti(tmp_path):
    """分时/五档/筹码/逐笔/F10 对 `.TI` 一律 400（旧端同：门面认不出 .TI）。"""
    c = _client_for(tmp_path, index_rows=[])
    for p in ("/api/intraday/881121.TI", "/api/intraday_multi/881121.TI",
              "/api/quote5/881121.TI", "/api/volume_profile/881121.TI",
              "/api/volume_ticks/881121.TI", "/api/f10_brief/881121.TI"):
        assert c.get(p).status_code == 400, p


# ---------------------------------------------------------------- 6. stale 口径


def test_index_stale_when_db_behind_expected(tmp_path):
    """stale=True：库里最新一根 < 「应收到的最后一根」。"""
    ctx = _mk_ctx(tmp_path, quotes=_FakeQuotes(),
                  index_rows=_idx_rows(["20260105", "20260106"]))
    out = backend.build_index_kline(ctx, INDEX_CODE, period="d",
                                    today8="20261023", now=datetime(2026, 10, 23, 16, 0))
    assert out["stale"] is True
    assert out["asof"] == "20260106"


def test_index_not_stale_after_close(tmp_path):
    """15:05 之后、库里已有当日根 → stale=False。"""
    ctx = _mk_ctx(tmp_path, quotes=_FakeQuotes(),
                  index_rows=_idx_rows(["20261022", "20261023"]))
    out = backend.build_index_kline(ctx, INDEX_CODE, period="d",
                                    today8="20261023", now=datetime(2026, 10, 23, 16, 0))
    assert out["stale"] is False


def test_index_stale_during_session_expects_prev_day(tmp_path):
    """盘中（<15:05）当日根尚未落库 → 应收到的最后一根是上一交易日，不算 stale。"""
    ctx = _mk_ctx(tmp_path, quotes=_FakeQuotes(),
                  index_rows=_idx_rows(["20261022", "20261023"]))
    out = backend.build_index_kline(ctx, INDEX_CODE, period="d",
                                    today8="20261023", now=datetime(2026, 10, 23, 10, 0))
    # 盘中：expected=20261022，库里最新=20261023 → 不 stale
    assert out["stale"] is False


def test_index_expected_latest_skips_weekend(tmp_path):
    """非交易日/周末：应收到的最后一根回退到上一交易日（这里只验「不会是周末」）。"""
    got = backend._index_expected_latest_date8("20261025", now=datetime(2026, 10, 25, 12, 0))
    assert got == "20261023", got          # 周日 → 上周五


# ---------------------------------------------------------------- 7. AMV 副图


def test_index_amv_subchart_from_sector_daily(tmp_path):
    """AMV 副图读 `amv_board.db::sector_daily`：元 → 亿元；date 有横线/bar 无横线的坑。"""
    days = [_day(9), _day(10), _day(11)]
    rows = _idx_rows(days)
    sector = [("%s-%s-%s" % (d[:4], d[4:6], d[6:8]), "L2", INDEX_CODE, 1.65e12 + i)
              for i, d in enumerate(days)]
    c = _client_for(tmp_path, index_rows=rows, sector_rows=sector)
    body = c.get("/api/kline/%s?period=d" % INDEX_CODE).json()
    assert [b["amv"] for b in body["bars"]] == [
        round((1.65e12 + i) / 1e8, 2) for i in range(3)], [b["amv"] for b in body["bars"]]
    assert body["bars"][0]["amvMa"] is None      # 不足 10 个有效值
    assert body["bars"][0]["amvWhite"] is not None


def test_index_amv_missing_table_degrades(tmp_path):
    """sector_daily 取不到 → amv 三列 None（不编造）。"""
    c = _client_for(tmp_path, index_rows=_idx_rows([_day(9)]))   # 不建 amv_board.db
    bar = c.get("/api/kline/%s?period=d" % INDEX_CODE).json()["bars"][0]
    assert bar["amv"] is None and bar["amvMa"] is None and bar["amvWhite"] is None


def test_index_week_amv_is_null_like_old(tmp_path):
    """周线的 AMV 副图三列恒 None——旧端 `_kline_payload_out` 只在 `period == "d"` 时
    调 `_amv_series_out`，周线固定 `[None] * len(bars)`。照搬（不是取不到）。"""
    q = _FakeQuotes()
    q.ths_index_weekly = lambda code, s, e: [
        {"t": "20261002", "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "v": 10.0, "amount": 100.0},
        {"t": "20261009", "o": 1.5, "h": 3.0, "l": 1.0, "c": 2.5, "v": 20.0, "amount": 200.0},
    ]
    sector = [("2026-10-02", "L2", INDEX_CODE, 1.0e12),
              ("2026-10-09", "L2", INDEX_CODE, 2.0e12)]
    c = _client_for(tmp_path, quotes=q, index_rows=[], sector_rows=sector)
    body = c.get("/api/kline/%s?period=w" % INDEX_CODE).json()
    assert [b["amv"] for b in body["bars"]] == [None, None]
    assert [b["amvMa"] for b in body["bars"]] == [None, None]
    assert [b["amvWhite"] for b in body["bars"]] == [None, None]


# ---------------------------------------------------------------- 8. 白名单 / 契约


def test_ths_index_db_in_core_whitelist():
    from app.core import paths
    assert "ths_index.db" in paths.READABLE_DB
    assert paths.READABLE_DB["ths_index.db"] == paths.DATA_DIR / "ths_index.db"


def test_manifest_declares_ths_index_and_version_bumped():
    raw = json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))
    m = validate_manifest(raw, PLUGIN_DIR)
    assert "ths_index.db" in m.data_read
    assert "read:ths_index" in m.backend.permissions
    assert m.version == "0.2.0", m.version


def test_index_code_detection():
    assert backend.is_index_kline_code("881121.TI") is True
    assert backend.is_index_kline_code(" 884123.ti ") is True
    assert backend.is_index_kline_code("600519.SH") is False
    assert backend.is_index_kline_code("000001.SH") is False
    assert backend.is_index_kline_code(None) is False
