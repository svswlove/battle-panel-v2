# -*- coding: utf-8 -*-
"""选股卡「盘中刷新优先」口径（2026-11-04）。

跑法：cd projects/panel-v2 && python -m pytest app/tests/test_selection_live_priority.py -q
边界：纯内存假 ctx，不读生产文件、不连库、不写盘。

口径（和 compat_misc.build_selection 的实现一一对应）：
  · 盘中产物**当日有效**（meta.mode=intraday 且 trade_date=今天）才参与竞争；
  · 盘后快照已经是今天 ⇒ 以盘后为准（收盘定稿不能被盘中预估盖掉）；
  · 否则用盘中产物，并在返回里带 mode=intraday（前端据此挂横幅）。
"""
import os
import sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.plugins.compat_misc import backend  # noqa: E402


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _yday():
    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


def _snap(date, n_rows=1):
    return {"meta": {"schema_version": "1.0.0", "document_type": "selection",
                     "signal_date": date, "generated_at": date + " 16:58:00"},
            "date": date,
            "asof": {"signal_date": date},
            "stats": {"hits": n_rows, "groups": 1, "scored": 0},
            "groups": [{"key": "B1", "name": "B1", "count": n_rows,
                        "rows": [{"code": "000001.SZ"}][:n_rows]}]}


def _live(date, mode="intraday"):
    d = _snap(date)
    d["meta"].update({"mode": mode, "trade_date": date, "generated_at": date + " 14:47:00",
                      "producer": "selection_live_refresh"})
    return d


class _Ctx:
    """最小 ctx：panel_json 按名字返回内存字典；缺失抛 FileNotFoundError（与 core 同行为）。"""

    def __init__(self, docs):
        self.docs = docs
        self.calls = []

    def panel_json(self, name):
        self.calls.append(name)
        if name not in self.docs:
            raise FileNotFoundError(name)
        return self.docs[name]


def test_live_used_when_snapshot_is_yesterday():
    ctx = _Ctx({"selection_latest": _snap(_yday()), "selection_live": _live(_today())})
    out = backend.build_selection(ctx)
    assert out["mode"] == "intraday"
    assert out["date"] == _today()
    assert out["freshness"]["mode"] == "intraday"
    assert out["freshness"]["state"] == "fresh"
    assert "pool" in out


def test_postclose_wins_when_snapshot_is_today():
    ctx = _Ctx({"selection_latest": _snap(_today()), "selection_live": _live(_today())})
    out = backend.build_selection(ctx)
    assert out["mode"] == "postclose", "盘后已产今日快照时，盘中预估不能盖它"


def test_live_from_yesterday_is_ignored():
    ctx = _Ctx({"selection_latest": _snap(_yday()), "selection_live": _live(_yday())})
    out = backend.build_selection(ctx)
    assert out["mode"] == "postclose"
    assert out["date"] == _yday()


def test_live_wrong_mode_is_ignored():
    ctx = _Ctx({"selection_latest": _snap(_yday()), "selection_live": _live(_today(), mode="postclose")})
    out = backend.build_selection(ctx)
    assert out["mode"] == "postclose"


def test_missing_live_falls_back_to_snapshot():
    ctx = _Ctx({"selection_latest": _snap(_yday())})
    out = backend.build_selection(ctx)
    assert out["mode"] == "postclose"
    assert out["date"] == _yday()


def test_selection_live_endpoint_without_artifact_returns_skeleton():
    ctx = _Ctx({})
    out = backend.build_selection_live(ctx)
    assert out["groups"] == []
    assert out["error"]
    assert out["freshness"]["state"] == "missing"


def test_selection_live_endpoint_returns_live_doc():
    ctx = _Ctx({"selection_live": _live(_today())})
    out = backend.build_selection_live(ctx)
    assert out["mode"] == "intraday"
    assert out["date"] == _today()
    assert out["groups"][0]["key"] == "B1"
