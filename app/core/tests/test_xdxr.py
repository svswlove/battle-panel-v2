# -*- coding: utf-8 -*-
"""除权除息事件（M1 B2）测试：`app/core/quotes.py::xdxr()` 的字段映射与降级。

口径唯一源：旧 8800 `api_kline_xdxr`（`engine/server.py:672`，pytdx `get_xdxr_info`）。
这里锁的是**最容易搬错的一层**：pytdx 的 `fenhong/songzhuangu/peigu/peigujia`
与 eltdx 的 `c1..c4` 顺序**不对应**（c2↔peigujia、c3↔songzhuangu、c4↔peigu），
按序号抄就会得到「分红 6.0 元、送股 1.0 股」这种看着像对、实际把价格当股数的错值。
2026-10-24 实测 7 只票 0 差异的映射就是下面这一份。

上游全部 mock（`quotes.call` 换成假 block）：不连行情源、不读缓存文件。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import quotes  # noqa: E402


class _Rec:
    """eltdx CapitalChangeRecord 的最小替身。"""

    def __init__(self, date_raw: int, category: int, c1: Any = None, c2: Any = None,
                 c3: Any = None, c4: Any = None) -> None:
        self.date_raw = date_raw
        self.category_raw = category
        self.category_name = quotes.XDXR_CATEGORY.get(category, str(category))
        self.c1_float = c1
        self.c2_float = c2
        self.c3_float = c3
        self.c4_float = c4


class _Block:
    def __init__(self, recs: List[_Rec]) -> None:
        self.records = recs


@pytest.fixture(autouse=True)
def _clean_cache() -> None:
    quotes._xdxr_cache.clear()
    yield
    quotes._xdxr_cache.clear()


def _patch_call(monkeypatch: pytest.MonkeyPatch, block: Any,
                calls: List[str] | None = None) -> None:
    def _call(fn_name: str, *args: Any, **kwargs: Any) -> Any:
        if calls is not None:
            calls.append("%s%s" % (fn_name, list(args)))
        return block

    monkeypatch.setattr(quotes, "call", _call)


# ---------------------------------------------------------------- 字段映射


def test_dividend_field_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    """category=1：fenhong=c1、songzhuangu=c3、peigu=c4、peigujia=c2（**不是** 1/2/3/4）。"""
    _patch_call(monkeypatch, _Block([
        _Rec(20260626, 1, c1=280.24, c2=0.0, c3=0.0, c4=0.0),
        _Rec(20020725, 1, c1=6.0, c2=0.0, c3=1.0, c4=0.0),
        _Rec(19910502, 1, c1=3.0, c2=0.0, c3=4.0, c4=0.0),
    ]))
    events = quotes.xdxr("600519.SH")
    assert [e["date"] for e in events] == ["19910502", "20020725", "20260626"], "按日期升序"
    by_date = {e["date"]: e for e in events}
    assert by_date["20020725"] == {
        "date": "20020725", "category": 1, "type": "除权除息",
        "fenhong": 6.0, "songzhuangu": 1.0, "peigu": 0.0, "peigujia": 0.0,
    }
    assert by_date["19910502"]["songzhuangu"] == 4.0, "送股取 c3"


def test_peigu_and_price_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    """1990 上证那条经典记录：配股 1.0 股 @ 3.56 元 → 必须落在 peigu/peigujia 上。"""
    _patch_call(monkeypatch, _Block([
        _Rec(19900301, 1, c1=0.0, c2=3.559999942779541, c3=0.0, c4=1.0),
    ]))
    ev = quotes.xdxr("000001.SZ")[0]
    assert ev["fenhong"] == 0.0
    assert ev["songzhuangu"] == 0.0
    assert ev["peigu"] == 1.0, "配股股数取 c4"
    assert ev["peigujia"] == pytest.approx(3.559999942779541), "配股价取 c2"


def test_non_dividend_categories_have_null_numbers(monkeypatch: pytest.MonkeyPatch) -> None:
    """category≠1（送配股上市/股本变化…）：四个数字一律 None（旧端 pytdx 同口径）。"""
    _patch_call(monkeypatch, _Block([
        _Rec(19910403, 5, c1=0.0, c2=0.0, c3=2650.0, c4=4850.017),
        _Rec(20200716, 2, c1=0.0, c2=0.0, c3=1.0, c4=2.0),
    ]))
    events = quotes.xdxr("000001.SZ")
    assert events[0]["type"] == "股本变化"
    for ev in events:
        assert ev["fenhong"] is None and ev["songzhuangu"] is None
        assert ev["peigu"] is None and ev["peigujia"] is None


def test_unknown_category_falls_back_to_number(monkeypatch: pytest.MonkeyPatch) -> None:
    """分类表外（如 99）→ type 就是分类号字符串（旧端 `str(r.get("category"))` 同）。"""
    _patch_call(monkeypatch, _Block([_Rec(20260101, 99)]))
    ev = quotes.xdxr("600000.SH")[0]
    assert ev["type"] == "99" and ev["category"] == 99


def test_illegal_date_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """日期越界（1980 年 / 13 月）→ 整条跳过（旧端同样 continue）。"""
    _patch_call(monkeypatch, _Block([
        _Rec(19800101, 1, c1=1.0),
        _Rec(20261301, 1, c1=1.0),
        _Rec(20260626, 1, c1=1.0),
    ]))
    events = quotes.xdxr("600519.SH")
    assert [e["date"] for e in events] == ["20260626"]


# ---------------------------------------------------------------- 市场判定（照搬旧端）


def test_market_routing_matches_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_tdx_market` 只看数字前缀，**不看后缀**（旧端历史口径，逐字照搬）。

    2026-10-24 R5：`000001.SH`（指数）不再走这条（`xdxr()` 入口先拦），
    但 `_tdx_market` 函数本身的旧口径不变（个股/基金/可转债共用）。
    """
    calls: List[str] = []
    _patch_call(monkeypatch, _Block([]), calls)
    for code, expect in (("600519.SH", "sh600519"), ("000001.SZ", "sz000001"),
                         ("300750.SZ", "sz300750"),
                         ("688981.SH", "sh688981"), ("510300.SH", "sh510300")):
        calls.clear()
        quotes._xdxr_cache.clear()
        quotes.xdxr(code)
        assert calls == ["corporate.capital_changes['%s']" % expect], (code, calls)


# ---------------------------------------------------------------- 指数（R5 修正旧端缺陷）


def test_index_returns_empty_without_quoting(monkeypatch: pytest.MonkeyPatch) -> None:
    """指数无除权除息 → 空数组，且**不打行情源**（不再串到平安银行）。

    旧端 `000001.SH` 会被判成深市去取 `sz000001`（平安银行）的 81 条记录；
    v2 在这里修正（详见 `_is_index` 与 HANDOFF_R5）。
    """
    calls: List[str] = []
    _patch_call(monkeypatch, _Block([_Rec(20260626, 1, c1=1.0)]), calls)
    for code in ("000001.SH", "399001.SZ", "000300.SH", "399006.SZ", "000905.SH"):
        assert quotes.xdxr(code) == [], code
    assert calls == [], "指数不该连行情源"


def test_index_detection_requires_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    """指数判据**带后缀**：`000001.SZ` 是平安银行（个股），仍照常取数。"""
    calls: List[str] = []
    _patch_call(monkeypatch, _Block([_Rec(20260626, 1, c1=1.0)]), calls)
    assert quotes.xdxr("000001.SZ") != [], "000001.SZ 是个股，不能当指数"
    assert calls == ["corporate.capital_changes['sz000001']"]
    calls.clear()
    quotes._xdxr_cache.clear()
    assert quotes.xdxr("600000.SH") != [], "沪市个股不受影响"
    assert calls == ["corporate.capital_changes['sh600000']"]


def test_bare_index_prefix_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """无后缀的 880/881 通达信板块指数 → 空表（旧端判不出市场，本就返回空）。"""
    calls: List[str] = []
    _patch_call(monkeypatch, _Block([_Rec(20260626, 1, c1=1.0)]), calls)
    assert quotes.xdxr("880005") == []
    assert quotes.xdxr("881101") == []
    assert calls == []


def test_bj_and_unknown_market_return_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """920xxx（北交所）与表外段 → 空列表，且**不打行情源**（旧端 `_tdx_market` 返 None）。"""
    calls: List[str] = []
    _patch_call(monkeypatch, _Block([_Rec(20260101, 1, c1=1.0)]), calls)
    assert quotes.xdxr("920001.BJ") == []
    assert quotes.xdxr("abcdef") == []
    assert quotes.xdxr("") == []
    assert calls == [], "市场不支持时不该连行情源"


# ---------------------------------------------------------------- 缓存与降级


def test_cache_hits_within_same_day(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: List[str] = []
    _patch_call(monkeypatch, _Block([_Rec(20260626, 1, c1=1.0)]), calls)
    first = quotes.xdxr("600519.SH")
    second = quotes.xdxr("600519.SH")
    assert first == second and len(calls) == 1, "同一自然日只取一次"


def test_cache_key_is_per_code(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: List[str] = []
    _patch_call(monkeypatch, _Block([_Rec(20260626, 1, c1=1.0)]), calls)
    quotes.xdxr("600519.SH")
    quotes.xdxr("000001.SZ")
    assert len(calls) == 2, "不同标的各取一次"


def test_source_unavailable_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(quotes, "call", lambda *a, **k: None)
    assert quotes.xdxr("600519.SH") == [], "行情源不可用 → 空表（不编造）"


def test_reset_clears_xdxr_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: List[str] = []
    _patch_call(monkeypatch, _Block([_Rec(20260626, 1, c1=1.0)]), calls)
    quotes.xdxr("600519.SH")
    quotes.reset()
    _patch_call(monkeypatch, _Block([_Rec(20260626, 1, c1=1.0)]), calls)
    quotes.xdxr("600519.SH")
    assert len(calls) == 2, "reset 后应重新取数"


def test_category_table_matches_frozen_baseline() -> None:
    """分类中文表必须与**冻结基线**逐项一致（14 类）。

    2026-10-30：旧仓 `engine/server.py` 退役删除，本测试原先从该文件读 `_XDXR_CAT`
    做对拍。基线改为下面的字面量（取自退役前最后一版 server.py 的 `_XDXR_CAT`，
    与当时逐值一致），作用是**锁表**：表被改会红，不会静默漂成新的分类口径。
    """
    frozen = {
        1: "除权除息", 2: "送配股上市", 3: "非流通股上市", 4: "未知",
        5: "股本变化", 6: "增发新股", 7: "股份回购", 8: "增发新股上市",
        9: "转配股上市", 10: "可转债上市", 11: "扩缩股", 12: "非流通股缩股",
        13: "送认购权证", 14: "送认沽权证",
    }
    assert quotes.XDXR_CATEGORY == frozen
