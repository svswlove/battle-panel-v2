# -*- coding: utf-8 -*-
"""独立条件单评估进程（app/cond_watch_v2.py）+ 面板让位 契约测试（2026-10-28）

守的是任务书的三条验收：
  ① 心跳让位：面板 `_on_quotes` 在独立进程心跳新鲜时跳过 evaluate/match_pending，
     心跳缺失/过期时兜底；audit 只在状态切换时写一次（feed 50ms 一次，不能每 tick 写）；
  ② 独立进程一轮评估：假 quotes 触发一条条件单 → 账本状态 TRIGGERED + enqueue 被调；
  ③ 盘外不评估：非交易时段直接 skipped，**一次行情都不取**。

全部用 `PANEL_V2_PAPER_DIR` 指到临时目录 + `HEARTBEAT_FILE` 指到 tmp_path，不碰生产账本。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]          # panel-v2
APP = Path(__file__).resolve().parents[1]           # panel-v2/app
for _p in (str(ROOT), str(APP)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import cond_watch_v2 as cw  # noqa: E402
from app.core.paper import service as paper_service  # noqa: E402
from app.core.paper import state  # noqa: E402

CODE = "600519.SH"


# ---------------------------------------------------------------- 夹具


@pytest.fixture()
def svc(tmp_path, monkeypatch):
    """临时账本 + 固定「交易时段」（真实时段另有专测）。"""
    monkeypatch.setenv("PANEL_V2_PAPER_DIR", str(tmp_path / "paper"))
    cw.reset_service()
    s = paper_service.build_service(initial_cash=100000.0, session_ok=lambda: True)
    yield s
    cw.reset_service()


@pytest.fixture()
def hb(tmp_path, monkeypatch):
    """把面板侧心跳路径指到 tmp，并清空 3s 判定缓存。"""
    path = tmp_path / "cond_watch_heartbeat.json"
    monkeypatch.setattr(paper_service, "_cond_watch_heartbeat_file", lambda: path)
    _reset_panel_cache()
    return path


def _reset_panel_cache() -> None:
    paper_service._COND_WATCH_CACHE.update({"at": 0.0, "alive": False})


def _spy(svc):
    """把 evaluate / match_pending 换成计数器（实例属性遮蔽方法）。"""
    box = {"eval": 0, "match": 0}

    def _eval(_quotes):
        box["eval"] += 1
        return {"triggered": [], "cancelled": [], "expired": []}

    def _match(_quotes):
        box["match"] += 1
        return []

    svc.evaluate = _eval
    svc.match_pending = _match
    return box


def _audit_events(svc) -> list[str]:
    path = Path(svc.state_dir) / "audit.log"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(str(json.loads(line).get("event")))
        except ValueError:
            continue
    return out


def _write_raw_hb(path: Path, *, ts: float, ok: bool = True, pid: int = 4242) -> None:
    path.write_text(json.dumps({"ts": ts, "pid": pid, "ok": ok, "round_ms": 7}),
                    encoding="utf-8")


TICK = {CODE: {"last": 10.0, "fresh": True, "limit_up": 11.0, "limit_down": 9.0}}


# ---------------------------------------------------------------- ① 面板让位


def test_panel_yields_when_heartbeat_fresh(svc, hb) -> None:
    box = _spy(svc)
    assert cw.write_heartbeat(True, 12, path=hb) is True
    _reset_panel_cache()

    svc.on_quotes(TICK)
    svc.on_quotes(TICK)          # 第二次不得再写 audit（3s 缓存内）

    assert box == {"eval": 0, "match": 0}
    assert _audit_events(svc).count("feed.yield_to_cond_watch") == 1


def test_panel_falls_back_when_heartbeat_stale(svc, hb) -> None:
    box = _spy(svc)
    _write_raw_hb(hb, ts=time.time() - 60)      # 过期 60s
    _reset_panel_cache()

    svc.on_quotes(TICK)

    assert box == {"eval": 1, "match": 1}


def test_panel_falls_back_when_heartbeat_missing(svc, hb) -> None:
    box = _spy(svc)
    assert not hb.exists()
    _reset_panel_cache()

    svc.on_quotes(TICK)

    assert box == {"eval": 1, "match": 1}


def test_panel_resumes_and_logs_once_after_yield(svc, hb) -> None:
    """让位 → 心跳过期 → 恢复：两个方向各只写一条 audit。"""
    box = _spy(svc)
    cw.write_heartbeat(True, 5, path=hb)
    _reset_panel_cache()
    svc.on_quotes(TICK)
    assert box["eval"] == 0

    _write_raw_hb(hb, ts=time.time() - 120)     # 独立进程死了
    _reset_panel_cache()
    svc.on_quotes(TICK)
    svc.on_quotes(TICK)

    assert box["eval"] == 2
    events = _audit_events(svc)
    assert events.count("feed.yield_to_cond_watch") == 1
    assert events.count("feed.resume_after_cond_watch") == 1


def test_panel_does_not_yield_on_ok_false(svc, hb) -> None:
    """ok=False（独立进程异常/优雅退出）必须立刻交回面板。"""
    box = _spy(svc)
    cw.write_heartbeat(False, 0, path=hb)
    _reset_panel_cache()

    svc.on_quotes(TICK)

    assert box == {"eval": 1, "match": 1}


def test_heartbeat_path_and_window_agree() -> None:
    """面板与独立进程必须指向同一文件、同一判活窗口（防两边各改一处漂移）。"""
    assert paper_service._cond_watch_heartbeat_file() == cw.HEARTBEAT_FILE
    assert paper_service._COND_WATCH_MAX_AGE_S == cw.HEARTBEAT_MAX_AGE_S


# ---------------------------------------------------------------- ② 一轮评估


def test_round_triggers_condition_and_pushes(svc, monkeypatch) -> None:
    cond = svc.add_condition("600519", "breakout_buy", 100, trigger_price=11.0,
                             quote={"last": 10.0}, name="贵州茅台")
    assert cond["status"] == "ACTIVE"

    sent = []
    monkeypatch.setattr(cw, "enqueue",
                        lambda *a, **k: sent.append({"args": a, "kw": k}) or {"ok": True})
    monkeypatch.setattr(cw, "is_trading_day", lambda _day: True)   # 不连 eltdx 日历

    quote = {CODE: {"last": 11.2, "fresh": True, "limit_up": 12.0, "limit_down": 9.0}}
    st = cw.evaluate_once(svc=svc, now=datetime(2026, 10, 28, 10, 0), quote_fn=lambda c: quote.get(c, {}))

    assert st["skipped"] is None and st["error"] is None
    assert (st["conditions"], st["codes"], st["quoted"]) == (1, 1, 1)
    assert st["triggered"] == 1 and st["pushed"] == 1

    trig = svc.list_conditions(status="TRIGGERED")
    assert len(trig) == 1
    assert trig[0]["cond_id"] == cond["cond_id"]
    assert trig[0]["trigger_price"] == 11.2
    assert trig[0]["order_id"]

    assert len(sent) == 1
    args, kw = sent[0]["args"], sent[0]["kw"]
    assert args[0] == "COND_TRIGGERED"
    assert "贵州茅台" in args[1] and CODE in args[1]
    assert "突破买入" in args[2] and "11.00" in args[2] and "11.20" in args[2]
    assert kw["dedup_key"] == "%s:%s:11.20" % (cond["cond_id"], time.strftime("%Y%m%d"))


def test_push_failure_does_not_break_ledger(svc, monkeypatch) -> None:
    """契约 §4：推送失败不影响账本状态。"""
    cond = svc.add_condition("600519", "breakout_buy", 100, trigger_price=11.0,
                             quote={"last": 10.0}, name="贵州茅台")
    monkeypatch.setattr(cw, "is_trading_day", lambda _day: True)

    def _boom(*_a, **_k):
        raise RuntimeError("notify 挂了")

    monkeypatch.setattr(cw, "enqueue", _boom)
    quote = {CODE: {"last": 11.2, "fresh": True, "limit_up": 12.0, "limit_down": 9.0}}
    st = cw.evaluate_once(svc=svc, now=datetime(2026, 10, 28, 10, 0), quote_fn=lambda c: quote.get(c, {}))

    assert st["error"] is None and st["triggered"] == 1 and st["pushed"] == 0
    assert svc.list_conditions(status="TRIGGERED")[0]["cond_id"] == cond["cond_id"]


def test_round_survives_quote_errors(svc, monkeypatch) -> None:
    """单票取价失败只跳过该票，不影响其它票（行情抖动不能杀死监控）。"""
    svc.add_condition("600519", "breakout_buy", 100, trigger_price=11.0,
                      quote={"last": 10.0}, name="贵州茅台")
    monkeypatch.setattr(cw, "is_trading_day", lambda _day: True)
    monkeypatch.setattr(cw, "enqueue", lambda *a, **k: {"ok": True})

    def _quote(code):
        raise RuntimeError("eltdx 超时")

    st = cw.evaluate_once(svc=svc, now=datetime(2026, 10, 28, 10, 0), quote_fn=_quote)
    assert st["error"] is None and st["quoted"] == 0 and st["triggered"] == 0


# ---------------------------------------------------------------- ③ 盘外不评估


def test_off_session_weekend_does_not_quote(svc) -> None:
    called = []
    st = cw.evaluate_once(svc=svc, now=datetime(2026, 10, 31, 10, 0),   # 周六
                          quote_fn=lambda c: called.append(c) or {})
    assert st["skipped"] == "closed" and called == [] and st["quoted"] == 0


def test_off_session_after_hours_does_not_quote(svc, monkeypatch) -> None:
    monkeypatch.setattr(cw, "is_trading_day", lambda _day: True)
    called = []
    st = cw.evaluate_once(svc=svc, now=datetime(2026, 10, 28, 20, 0),   # 交易日 20:00
                          quote_fn=lambda c: called.append(c) or {})
    assert st["skipped"] == "closed" and called == []


# ---------------------------------------------------------------- 心跳 / 循环 / 单实例


def test_heartbeat_write_read(tmp_path) -> None:
    path = tmp_path / "hb.json"
    assert cw.write_heartbeat(True, 12, path=path) is True
    doc = cw.read_heartbeat(path)
    assert doc["ok"] is True and doc["pid"] == os.getpid() and doc["round_ms"] == 12
    assert cw.heartbeat_fresh(doc) is True
    assert cw.heartbeat_fresh(doc, now=time.time() + 60) is False        # 过期
    cw.write_heartbeat(False, 0, path=path)
    assert cw.heartbeat_fresh(cw.read_heartbeat(path)) is False         # ok=False
    assert cw.heartbeat_fresh(None) is False
    assert cw.heartbeat_fresh({"ts": "bad", "ok": True}) is False


def test_run_forever_writes_heartbeat_and_stops_on_event(svc, monkeypatch, tmp_path) -> None:
    hbp = tmp_path / "hb.json"
    monkeypatch.setattr(cw, "panel_online", lambda *a, **k: False)     # 不探 8810
    stop = threading.Event()

    def _eval(**_kw):
        stop.set()                                                     # 第一轮后退出
        return {"at": "x", "skipped": "closed", "triggered": 0, "error": None}

    monkeypatch.setattr(cw, "evaluate_once", _eval)
    assert cw.run_forever(interval=1, svc=svc, stop=stop, heartbeat_path=hbp) == 0

    doc = cw.read_heartbeat(hbp)
    assert doc["ok"] is False and doc["state"] == "stopped" and doc["rounds"] == 1
    assert cw.heartbeat_fresh(doc) is False


def test_run_forever_records_round_error(svc, monkeypatch, tmp_path) -> None:
    hbp = tmp_path / "hb.json"
    monkeypatch.setattr(cw, "panel_online", lambda *a, **k: False)
    stop = threading.Event()

    def _eval(**_kw):
        stop.set()
        return {"at": "x", "skipped": None, "triggered": 0, "error": "RuntimeError: boom"}

    monkeypatch.setattr(cw, "evaluate_once", _eval)
    cw.run_forever(interval=1, svc=svc, stop=stop, heartbeat_path=hbp)

    doc = cw.read_heartbeat(hbp)
    assert doc["ok"] is False and doc["last_error"] == "RuntimeError: boom"
    assert "cond_watch.round_error" in _audit_events(svc)


def test_single_instance_guard(tmp_path, monkeypatch) -> None:
    path = tmp_path / "hb.json"
    _write_raw_hb(path, ts=time.time() - 120)          # 过期 → 允许启动
    cw.single_instance_guard(path)

    _write_raw_hb(path, ts=time.time(), pid=999999)    # 新鲜 + 进程活着 → 拒绝
    monkeypatch.setattr(cw, "_pid_alive", lambda _pid: True)
    with pytest.raises(SystemExit):
        cw.single_instance_guard(path)

    _write_raw_hb(path, ts=time.time(), pid=os.getpid())   # 自己的心跳 → 允许
    cw.single_instance_guard(path)
