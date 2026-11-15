# -*- coding: utf-8 -*-
"""`/sector_track.json` 的 `idx_chg` 读路径补值（N20261027-100741-29 等 4 条标注）。

标注事实：生产轨迹文件的快照只有 `ts / all / boards`，**没有 `idx_chg`**（旧契约里有），
前端「行情涨跌」模式六个指数全空。修法：在 `/sector_track.json` 读路径按 ts 分钟补，
不写生产文件、不改其它字段；对不齐的日期保留缺字段（不补 0、不拿今天曲线贴历史）。

本文件只测纯函数与端点，不碰生产库、不写 data/panel。
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

PLUGIN_DIR = Path(__file__).resolve().parents[1]
BACKEND_PY = PLUGIN_DIR / "backend.py"
PROJECT_ROOT = PLUGIN_DIR.parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(scope="module")
def backend():
    spec = importlib.util.spec_from_file_location("compat_sector_backend_idxfill", BACKEND_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _clear_idx_cache(backend):
    """每个用例前清 20s 缓存（否则前一个用例的合成行情会串到下一个）。"""
    backend._IDX_CACHE.clear()
    yield
    backend._IDX_CACHE.clear()


# ---------------------------------------------------------------- 行情门面替身

class _Bar:
    def __init__(self, day: dt.date, hhmm: str, close: float, open_: float | None = None):
        hh, mm = int(hhmm[:2]), int(hhmm[3:5])
        self.time = dt.datetime(day.year, day.month, day.day, hh, mm)
        self.close = close
        self.open = close if open_ is None else open_


class _Quotes:
    """最小行情门面：`normalize / minute_bars / daily_bars`（与 core `ctx.quotes` 同名同形）。"""

    def __init__(self, minutes: Dict[str, List[_Bar]], dailies: Dict[str, List[_Bar]]):
        self._minutes = minutes
        self._dailies = dailies
        self.calls: List[str] = []

    def normalize(self, code: str) -> str:
        body, _, sfx = str(code).partition(".")
        return ("sh" if sfx.upper() == "SH" else "sz") + body

    def minute_bars(self, glb: str, count: int = 241):
        self.calls.append("minute:" + glb)
        return list(self._minutes.get(glb, ()))

    def daily_bars(self, glb: str, count: int = 8, adjust=None):
        self.calls.append("daily:" + glb)
        return list(self._dailies.get(glb, ()))


class _Ctx:
    def __init__(self, panels: Dict[str, Any], quotes: Any = None):
        self.panels = panels
        self._quotes = quotes

    def panel_json(self, name: str):
        if name not in self.panels:
            raise FileNotFoundError(name)
        return self.panels[name]

    def db_read(self, name: str):
        raise FileNotFoundError(name)

    @property
    def quotes(self):
        if self._quotes is None:
            raise PermissionError("quotes 未注入")
        return self._quotes


DAY = dt.date(2026, 10, 27)
PREV = dt.date(2026, 10, 26)
HIST = dt.date(2026, 10, 23)


def _minutes(day: dt.date, start: str, n: int, base: float, step: float) -> List[_Bar]:
    """从 start 起每 1 分钟一根（跨 11:30→13:00 自动跳午休）。"""
    out = []
    hh, mm = int(start[:2]), int(start[3:5])
    for i in range(n):
        out.append(_Bar(day, "%02d:%02d" % (hh, mm), round(base + step * i, 3)))
        mm += 1
        if mm == 60:
            hh, mm = hh + 1, 0
        if (hh, mm) == (11, 31):
            hh, mm = 13, 1
    return out


def _daily(day: dt.date, close: float) -> _Bar:
    return _Bar(day, "15:00", close)


# 昨收 100；今天 09:31 起每分钟 +1 → 涨跌% 可手算：101 → +1.0%
PRE_CLOSE = 100.0
IDX = ["000001.SH", "399006.SZ", "000688.SH", "000300.SH", "000852.SH", "399303.SZ"]


def _quotes_all_six() -> _Quotes:
    minutes = {}
    dailies = {}
    for i, code in enumerate(IDX):
        glb = ("sh" if code.endswith(".SH") else "sz") + code[:6]
        minutes[glb] = _minutes(DAY, "09:31", 8, PRE_CLOSE + 1 + i, 1.0)
        dailies[glb] = [_daily(PREV, PRE_CLOSE), _daily(DAY, PRE_CLOSE + 9)]
    return _Quotes(minutes, dailies)


def _snaps(ts_list: List[str], extra: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
    out = []
    for ts in ts_list:
        s: Dict[str, Any] = {"ts": ts, "all": 0.5,
                             "boards": {"881101.TI": {"name": "种植业与林业", "amv_pct": 1.2}}}
        if extra:
            s.update(extra)
        out.append(s)
    return out


def _doc(snaps: List[Dict[str, Any]], date_key: str = "20261027") -> Dict[str, Any]:
    return {"meta": {"document_type": "amv_live_sector_track"}, "dates": {date_key: snaps}}


# ---------------------------------------------------------------- 纯函数

def test_fill_adds_idx_chg_and_keeps_original_fields(backend) -> None:
    """补 `idx_chg`，原有 ts/all/boards 一个不动；09:30 与 09:35 对齐到各自分钟。"""
    snaps = _snaps(["2026-10-27 09:30:56", "2026-10-27 09:35:02"])
    out = backend.fill_idx_chg(_Ctx({}, _quotes_all_six()), snaps, "20261027", today8="20261027")
    assert len(out) == 2
    assert out[0]["ts"] == "2026-10-27 09:30:56"
    assert out[0]["all"] == 0.5
    assert out[0]["boards"]["881101.TI"]["name"] == "种植业与林业"
    # 09:30 用当日开盘价（= 09:31 那根的 open = 101）→ +1.0%
    assert out[0]["idx_chg"]["000001.SH"] == pytest.approx(1.0, abs=1e-6)
    # 09:35 对齐到 09:35 那根（101+4 = 105）→ +5.0%
    assert out[1]["idx_chg"]["000001.SH"] == pytest.approx(5.0, abs=1e-6)
    # 六个指数都补上
    assert set(out[1]["idx_chg"]) == set(IDX)
    # 原 list 不被改（生产文件只读）
    assert "idx_chg" not in snaps[0] and "idx_chg" not in snaps[1]


def test_fill_pre_close_missing_keeps_field_absent(backend) -> None:
    """昨收取不到 → 保留缺字段，不补 0（不拿 0 冒充 0%）。"""
    q = _Quotes({}, {})          # 完全没有行情
    snaps = _snaps(["2026-10-27 09:35:02"])
    out = backend.fill_idx_chg(_Ctx({}, q), snaps, "20261027", today8="20261027")
    assert "idx_chg" not in out[0]


def test_fill_missing_pre_close_does_not_use_open_as_substitute(backend) -> None:
    """有分钟线但无独立昨收 → 不补值，不能用当日开盘伪造涨跌基准。"""
    glb = "sh000001"
    q = _Quotes({glb: [_Bar(DAY, "09:31", 101.0)]}, {glb: []})
    out = backend.fill_idx_chg(_Ctx({}, q), _snaps(["2026-10-27 09:35:02"]), "20261027", today8="20261027")
    assert "idx_chg" not in out[0]


def test_fill_null_close_does_not_become_zero(backend) -> None:
    """分钟收盘为 None 的点不能变成 0.0（0 会被读成「平盘」）。"""
    glb = "sh000001"
    bars = [_Bar(DAY, "09:31", 101.0), _Bar(DAY, "09:32", None), _Bar(DAY, "09:33", 103.0)]
    q = _Quotes({glb: bars}, {glb: [_daily(PREV, PRE_CLOSE)]})
    out = backend.fill_idx_chg(_Ctx({}, q), _snaps(["2026-10-27 09:32:30"]), "20261027", today8="20261027")
    # 09:32 缺值 → 回退到 ≤09:32 的最近有效值（09:31 的 101 → +1.0%），而不是 0
    assert out[0]["idx_chg"][glb_to_ts(glb)] == pytest.approx(1.0, abs=1e-6)


def glb_to_ts(glb: str) -> str:
    return glb[2:] + "." + glb[:2].upper()


def test_fill_0930_and_1300_alignment(backend) -> None:
    """09:30（开盘）与 13:00（午休后首点）各自对齐，不串到上午末点。"""
    glb = "sh000001"
    bars = [_Bar(DAY, "09:31", 101.0), _Bar(DAY, "11:29", 120.0),
            _Bar(DAY, "13:01", 130.0), _Bar(DAY, "13:05", 140.0)]
    q = _Quotes({glb: bars}, {glb: [_daily(PREV, PRE_CLOSE)]})
    out = backend.fill_idx_chg(
        _Ctx({}, q), _snaps(["2026-10-27 09:30:56", "2026-10-27 13:00:30", "2026-10-27 13:05:10"]),
        "20261027", today8="20261027")
    assert out[0]["idx_chg"][glb_to_ts(glb)] == pytest.approx(1.0, abs=1e-6)     # 开盘 101
    assert out[1]["idx_chg"][glb_to_ts(glb)] == pytest.approx(20.0, abs=1e-6)    # ≤13:00 的最近点 = 11:29
    assert out[2]["idx_chg"][glb_to_ts(glb)] == pytest.approx(40.0, abs=1e-6)    # 13:05


def test_fill_other_day_not_stitched(backend) -> None:
    """请求历史日期、行情只给今天 → 一根都不补（不拿今天曲线硬填历史）。

    且历史日期**一次行情都不打**（每指数 2 次调用上限，历史日应为 0 次）。
    """
    q = _quotes_all_six()        # 全是 2026-10-27 的分钟
    snaps = _snaps(["2026-10-23 09:35:02"])
    out = backend.fill_idx_chg(_Ctx({}, q), snaps, "20261023", today8="20261027")
    assert "idx_chg" not in out[0], "历史日期被今天的曲线硬填了"
    assert q.calls == [], "历史日期不该去拉分钟线（实际打了 %s）" % q.calls


def test_fill_existing_idx_chg_passthrough(backend) -> None:
    """旧文件已带 `idx_chg` → 原样透传，不被覆盖。"""
    keep = {"000001.SH": 1.23}
    snaps = _snaps(["2026-10-27 09:35:02"], extra={"idx_chg": keep})
    out = backend.fill_idx_chg(_Ctx({}, _quotes_all_six()), snaps, "20261027", today8="20261027")
    assert out[0]["idx_chg"] == keep


def test_fill_quotes_missing_degrades(backend) -> None:
    """没有行情门面 → 保留缺字段、不抛。"""
    snaps = _snaps(["2026-10-27 09:35:02"])
    out = backend.fill_idx_chg(_Ctx({}, None), snaps, "20261027", today8="20261027")
    assert "idx_chg" not in out[0]


def test_idx_series_map_fetches_each_index_once(backend) -> None:
    """每指数一次取数 + 20s 缓存：两次调用不重复打行情（不许每次刷新 6×全历史）。"""
    q = _quotes_all_six()
    ctx = _Ctx({}, q)
    backend._IDX_CACHE.clear()
    backend.idx_series_map(ctx, "20261027")
    n1 = len(q.calls)
    backend.idx_series_map(ctx, "20261027")
    assert len(q.calls) == n1, "第二次调用又打了行情（缓存没生效）"
    assert n1 == 6 * 2, "每指数应只取一次分钟 + 一次日线（实际 %d 次）" % n1


# ---------------------------------------------------------------- 端点

def test_endpoint_snapshots_carry_idx_chg(backend, monkeypatch) -> None:
    # 端点走 fill_idx_chg 的缺省 today8=今天；用 monkeypatch 固定成 20261027，
    # 否则跑测试的当天不是 2026-10-27 时会直接跳过补值（那是正确行为，但测不到补值）。
    real = backend.fill_idx_chg
    monkeypatch.setattr(backend, "fill_idx_chg",
                        lambda ctx, snaps, date8, today8=None: real(ctx, snaps, date8, "20261027"))
    doc = _doc(_snaps(["2026-10-27 09:30:56", "2026-10-27 09:35:02"]))
    ctx = _Ctx({"amv_live_sector_track": doc}, _quotes_all_six())
    backend._IDX_CACHE.clear()
    app = FastAPI()
    app.include_router(backend.register(ctx), prefix="/api")
    body = TestClient(app).get("/api/sector_track.json").json()
    assert body["date"] == "20261027"
    assert body["snapshots"][0]["idx_chg"]["000001.SH"] == pytest.approx(1.0, abs=1e-6)
    assert body["snapshots"][1]["idx_chg"]["000001.SH"] == pytest.approx(5.0, abs=1e-6)
    # 契约键一个不多一个不少（旧端正常分支 9 键，无 source）
    assert set(body) == {"date", "snapshots", "boards", "weights", "weights_date",
                         "stale", "stale_reason", "last_ts", "meta"}
    # boards 字段也没被污染
    assert set(body["boards"]["881101.TI"]) == {"name", "amv_pct"}


def test_endpoint_without_quotes_still_serves(backend) -> None:
    """行情不可用 → 端点照常 200，快照保留缺字段（前端给「无数据」说明）。"""
    doc = _doc(_snaps(["2026-10-27 09:35:02"]))
    backend._IDX_CACHE.clear()
    app = FastAPI()
    app.include_router(backend.register(_Ctx({"amv_live_sector_track": doc}, None)), prefix="/api")
    body = TestClient(app).get("/api/sector_track.json").json()
    assert body["snapshots"] and "idx_chg" not in body["snapshots"][0]
