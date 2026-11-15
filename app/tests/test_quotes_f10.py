# -*- coding: utf-8 -*-
"""core 行情门面 · F10 B2 段（`quotes.f10_brief`）契约测试。

不打真实网络：`_f10_client` 用假客户端替换，验证门面自己的逻辑
（Entry/参数、result_sets 取值、单项失败降级、重试一次、TTL 缓存、熔断、代理旁路）。
口径来源：旧 8800 `server.py::_eltdx_f10_brief()` 的 B2 半段（逐项移植）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import quotes as Q  # noqa: E402


# ---------------------------------------------------------------- 假 F10 客户端


class _Table:
    def __init__(self, rows):
        self.rows = tuple(rows)


class _Resp:
    def __init__(self, tables):
        self.result_sets = tuple(_Table(t) for t in tables)

    @property
    def rows(self):
        return self.result_sets[0].rows if self.result_sets else ()


class _FakeF10:
    """形状与 eltdx `F10Client` 一致（valuation/shareholder_change_plans/.../call）。"""

    def __init__(self, fail=(), empty=()):
        self.calls = []
        self.fail = set(fail)          # 命中的方法名/Entry 抛异常
        self.empty = set(empty)        # 命中的方法名/Entry 返回空表

    def _maybe_fail(self, name):
        if name in self.fail:
            raise RuntimeError("http 502: %s" % name)

    def valuation(self, code, req_id="200191", **kw):
        self.calls.append(("valuation", code, req_id))
        self._maybe_fail("valuation")
        if "valuation" in self.empty:
            return _Resp([[], []])
        return _Resp([
            [{"x": 1}],
            [{"DATE": "20261023", "PETTM": "20.49", "PEBFW": "19.06",
              "PBMRQ": "6.64", "PBBFW": "9.65",
              "AVGMVM": "1668858904576.00", "ALIQMV": "9999999999999.00"}],
        ])

    def shareholder_change_plans(self, code, **kw):
        self.calls.append(("plans", code))
        self._maybe_fail("plans")
        if "plans" in self.empty:
            return _Resp([[]])
        return _Resp([[{"N001": "20251230", "N002": "拟增持", "N003": "某集团",
                        "N004": "控股股东", "N011": "完成"},
                       {"N001": "20240101", "N002": "减持", "N003": "某人",
                        "N004": "高管", "N011": "完成"},
                       {"N001": "20230101", "N002": "减持", "N003": "旧事",
                        "N004": "高管", "N011": "完成"}]])

    def northbound_holding(self, code, section="bszj", **kw):
        self.calls.append(("north", code, section))
        self._maybe_fail("north")
        if "north" in self.empty:
            return _Resp([[]])
        return _Resp([[{"N001": "20260930", "N002": "4.42", "N005": "2.8896"}]])

    def profit_forecast(self, code, **kw):
        self.calls.append(("forecast", code))
        self._maybe_fail("forecast")
        if "forecast" in self.empty:
            return _Resp([[], [], [], [], []])
        return _Resp([
            [{"nyear": "2026"}],                       # t0：预测起始年
            [{"T036": "67.191", "T021": "17775958", "T033": "8442193"}],   # t1
            [{"T002": "2023", "T012": "10370866", "T018": "7473407.16"},
             {"T002": "2025", "T012": "11480895", "T018": "8232007"}],     # t2 历史（乱序输入）
            [],
            [{"t023": "39", "rq": "2026-10-01"}],       # t4 机构数
        ])

    def stock_score(self, code, section="pf", arg="", **kw):
        self.calls.append(("score", code, section))
        self._maybe_fail("score")
        if "score" in self.empty:
            return _Resp([[]])
        if section == "rzrq":
            return _Resp([[{"N001": "20261023", "N002": "-249968937",
                            "N003": "16104615203", "N004": "-900",
                            "N005": "277744080"},
                           {"N001": "bad-date", "N002": "1"}], []])
        return _Resp([[{"N012": "白酒"}]])

    def call(self, entry, body=None, *, params=None):
        self.calls.append(("call", entry, tuple(params or ())))
        self._maybe_fail(entry)
        if entry in self.empty:
            return _Resp([[]])
        if entry == "CWServ.tdxf10_gg_cwfx":
            return _Resp([[
                {"T002": "2026-06-30", "jyr": "17274370000", "jlrtbzzl": "-6.9",
                 "yysrtb": "-5.23", "mgsy": "13.8186", "xsmll": "89.26"},
                {"T002": "2026-03-31", "jyr": "27242510000", "jlrtbzzl": "1.47",
                 "yysrtb": "6.34", "mgsy": "21.7545", "xsmll": "89.76"},
            ]])
        if entry == "CWServ.tdxf10_gg_gdyj":
            return _Resp([[
                {"T002": "2026-06-30", "T003": "296404", "T004": "4217.4923",
                 "T005": "21.8972", "T006": "-18.107", "T007": "1185.49"},
                {"T002": "bad", "T003": "1"},
            ]])
        return _Resp([[]])


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    """每个用例：清内存缓存 + **把 B2 落盘缓存指到临时目录**。

    2026-10-26 加落盘缓存后必需隔开：否则用例会读到 `data/cache/f10_b2/`
    里真实跑出来的数据，拿到 PE=20.72 而不是假客户端给的 20.49（实测踩过）。
    """
    Q.reset()
    monkeypatch.setattr(Q, "F10_B2_CACHE_DIR", str(tmp_path / "f10_b2"))
    yield
    Q.reset()
    monkeypatch.setattr(Q, "F10_B2_CACHE_DIR", None)


def _install(monkeypatch, fake):
    monkeypatch.setattr(Q, "_f10_client", lambda: fake)


# ---------------------------------------------------------------- 正常路径


def test_f10_brief_fills_all_segments(monkeypatch) -> None:
    fake = _FakeF10()
    _install(monkeypatch, fake)
    out = Q.f10_brief("sh600519")
    assert out["errors"] == []
    assert out["val"] == {"pe_ttm": 20.49, "pe_pct": 19.06, "pb_mrq": 6.64,
                          "pb_pct": 9.65, "total_mcap": 9999999999999.0,
                          "float_mcap": 1668858904576.0}
    # 增减持：新→旧、只留 2 条
    assert [p["date"] for p in out["plans"]] == ["2025-12-30", "2024-01-01"]
    assert out["north"] == {"date": "2026-09-30", "ratio": 4.42, "chg_pct": 2.8896}
    assert out["forecast"] == {"year": 2026, "eps": 67.191, "org_count": 39}
    assert out["sector"] == "白酒"
    # 业绩历史：年份升序、万元→亿、同比只对有前值算；预测年（2027/2028）接在历史后
    assert out["perf"]["years"] == [2023, 2025, 2026, 2027, 2028]
    assert out["perf"]["revenue"][0] == pytest.approx(1037.0866, abs=1e-4)
    assert out["perf"]["yoy_rev"][0] is None
    # 融资融券：坏日期行跳过
    assert len(out["rzrq"]) == 1 and out["rzrq"][0]["date"] == "2026-10-23"
    # 季度业绩：新→旧；元→亿
    assert out["perf_q"]["periods"][0]["rq"] == "2026-06-30"
    assert out["perf_q"]["periods"][0]["net"] == pytest.approx(172.7437, abs=1e-4)
    assert out["perf_q"]["years"]["2026"]["count"] == 2
    # 股东户数序列
    assert out["gdhs"]["periods"][0]["holders"] == 296404
    # Entry / 参数照搬旧端
    entries = [c for c in fake.calls if c[0] == "call"]
    assert ("call", "CWServ.tdxf10_gg_cwfx", ("600519", "zyzb", "")) in entries
    assert ("call", "CWServ.tdxf10_gg_gdyj",
            ("600519", "gdrs", "", "", "1", "1", "40")) in entries
    assert ("valuation", "sh600519", "200191") in fake.calls


def test_f10_brief_caches_for_ttl(monkeypatch) -> None:
    """同一代码第二次不再打上游（内存层）。"""
    fake = _FakeF10()
    _install(monkeypatch, fake)
    Q.f10_brief("sh600519")
    n = len(fake.calls)
    Q.f10_brief("sh600519")           # 命中缓存：不再打上游
    assert len(fake.calls) == n


def test_f10_b2_survives_process_restart(monkeypatch) -> None:
    """2026-10-26 主人选型（方案二）：B2 落盘，**进程重启后仍不冷查**。

    这是本次改造的核心目的：面板常驻后台、偶尔看一眼，不该每次都等 7-12s。
    旧行为（F10_TTL=60s 纯内存）下，重启/关面板 = 内存全清 = 下一个请求必冷。

    验证手法：跑一次 → 模拟「进程重启」（清内存缓存，磁盘不动）→ 再跑，
    仍不得调上游。
    """
    fake = _FakeF10()
    _install(monkeypatch, fake)
    first = Q.f10_brief("sh600519")
    assert first["val"]["pe_ttm"] == 20.49
    n = len(fake.calls)

    # 模拟进程重启：只清内存，不碰磁盘
    Q._f10_cache.clear()
    again = Q.f10_brief("sh600519")

    assert len(fake.calls) == n, "进程重启后又去冷查了——落盘缓存没生效"
    assert again["val"] == first["val"], "从磁盘取回的值与首次不一致"
    assert again["perf_q"] and again["gdhs"], "落盘漏了段"


def test_f10_b2_expires_across_trading_day(monkeypatch) -> None:
    """跨交易日必须重拉（不能把昨天的基本面当今天用）。"""
    fake = _FakeF10()
    _install(monkeypatch, fake)
    Q.f10_brief("sh600519")
    n = len(fake.calls)

    # 把磁盘里的 trade_date 改成昨天 + 清内存 = 模拟隔日
    import json as _json
    import os as _os
    d = Q._f10_b2_dir()
    p = _os.path.join(d, "sh600519.json")
    with open(p, encoding="utf-8") as f:
        doc = _json.load(f)
    doc["trade_date"] = "20200101"
    with open(p, "w", encoding="utf-8") as f:
        _json.dump(doc, f, ensure_ascii=False)
    Q._f10_cache.clear()

    Q.f10_brief("sh600519")
    assert len(fake.calls) > n, "跨交易日仍读旧缓存，会拿昨天的 PE/股东户数当今天"


# ---------------------------------------------------------------- 降级路径


def test_single_segment_failure_keeps_others(monkeypatch) -> None:
    """单项失败（valuation 502）→ 该项空，其余段照常（不整批丢）。"""
    fake = _FakeF10(fail={"valuation"})
    _install(monkeypatch, fake)
    out = Q.f10_brief("sh600519")
    assert out["val"] == {}
    assert "valuation" in out["errors"]
    assert out["north"] and out["perf_q"] and out["gdhs"]
    assert out["forecast"]["eps"] == 67.191


def test_empty_tables_are_null_not_zero(monkeypatch) -> None:
    """0 行是正常无数据 → None/[]，不补 0（不编造）。"""
    fake = _FakeF10(empty={"valuation", "plans", "north", "forecast",
                           "CWServ.tdxf10_gg_cwfx", "CWServ.tdxf10_gg_gdyj"})
    _install(monkeypatch, fake)
    out = Q.f10_brief("sh600519")
    assert out["val"] == {}
    assert out["plans"] == [] and out["north"] is None and out["forecast"] is None
    assert out["perf"] is None and out["perf_q"] is None and out["gdhs"] is None


def test_retries_once_then_gives_up(monkeypatch) -> None:
    """失败重试 1 次（总 2 次尝试），仍失败则该项降级——不无限重试。"""
    state = {"n": 0}

    class _Flaky(_FakeF10):
        def valuation(self, code, req_id="200191", **kw):
            state["n"] += 1
            if state["n"] == 1:
                raise RuntimeError("first 502")
            return super().valuation(code, req_id=req_id, **kw)

    _install(monkeypatch, _Flaky())
    out = Q.f10_brief("sh600519")
    assert state["n"] == 2, "必须重试一次"
    assert out["val"]["pe_ttm"] == 20.49

    # 2026-10-26：第一次成功已落盘，必须连磁盘一起清，否则第二段直接读盘、
    # 根本不会去调上游（那样就测不到「总是失败」的重试口径了）。
    Q.reset()
    Q._f10_b2_clear("sh600519")

    class _Always(_FakeF10):
        def valuation(self, code, req_id="200191", **kw):
            state.setdefault("m", 0)
            state["m"] += 1
            raise RuntimeError("always 502")

    _install(monkeypatch, _Always())
    out2 = Q.f10_brief("sh600519")
    assert out2["val"] == {}
    assert state["m"] == 2, "只重试一次，不搞第三次"


def test_client_unavailable_degrades(monkeypatch) -> None:
    monkeypatch.setattr(Q, "_f10_client", lambda: None)
    out = Q.f10_brief("sh600519")
    assert out["val"] == {} and out["plans"] == []
    assert out["errors"], "客户端不可用必须留痕"


def test_circuit_opens_after_repeated_total_failure(monkeypatch) -> None:
    """整批全空连续 N 次 → 熔断窗口内直接返回（不再白等超时）。"""
    fake = _FakeF10(fail={"valuation", "plans", "north", "forecast", "score",
                          "CWServ.tdxf10_gg_cwfx", "CWServ.tdxf10_gg_gdyj"})
    _install(monkeypatch, fake)
    for _ in range(Q.F10_FAIL_STREAK):
        Q.f10_brief("sh600519")
    n = len(fake.calls)
    out = Q.f10_brief("sh600519")
    assert len(fake.calls) == n, "熔断窗口内不得再打上游"
    assert "circuit_open" in out["errors"]


def test_bad_code_returns_empty(monkeypatch) -> None:
    fake = _FakeF10()
    _install(monkeypatch, fake)
    out = Q.f10_brief("")
    assert out["errors"] == ["bad_code"] and not fake.calls


# ---------------------------------------------------------------- 代理旁路


def test_no_proxy_bypass_covers_f10_hosts(monkeypatch) -> None:
    """F10 调用期间 7615 网关必须在 no_proxy 里（代理一关就 WinError 10061）。

    ⚠ Windows 的 `os.environ` 大小写不敏感（写 no_proxy 就是写 NO_PROXY），
    所以断言只看合并后的值，不区分两个键。
    """
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    seen = {}
    orig = _FakeF10.valuation

    def spy(self, code, req_id="200191", **kw):
        seen["no_proxy"] = os.environ.get("no_proxy") or os.environ.get("NO_PROXY") or ""
        return orig(self, code, req_id=req_id, **kw)

    monkeypatch.setattr(_FakeF10, "valuation", spy)
    _install(monkeypatch, _FakeF10())
    Q.f10_brief("sh600519")
    assert "static.tdx.com.cn" in seen["no_proxy"]
    assert "hot.icfqs.com" in seen["no_proxy"]
    assert "localhost" in seen["no_proxy"], "原有条目必须保留"
    # 退出后还原（不污染其它请求）
    assert (os.environ.get("no_proxy") or os.environ.get("NO_PROXY")) == "localhost,127.0.0.1"
