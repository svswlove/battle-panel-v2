# -*- coding: utf-8 -*-
"""盘后预热器测试（2026-11-07）。

只测纯逻辑：清单解析、就绪判定、待跑任务筛选。线程/ASGI 自调由端到端验收覆盖。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import paths  # noqa: E402
from app.core import prewarm  # noqa: E402


@pytest.fixture
def out_dir(tmp_path, monkeypatch):
    d = tmp_path / "out"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(paths, "OUT_DIR", d)
    monkeypatch.setattr(paths, "PANEL_V2_DIR", tmp_path)   # 清单随代码走
    return d


def _write_plan(_out_dir, tasks, sources=("amv_board",)):
    p = prewarm.plan_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"version": 1, "ready_sources": list(sources), "tasks": tasks}),
                 encoding="utf-8")


def _board(trade_date="2026-11-06", freshness=None):
    return {
        "meta": {"trade_date": trade_date, "updated_at": "2026-11-06 16:31:00"},
        "freshness": freshness if freshness is not None else [
            {"key": "amv_board", "state": "新鲜"},
            {"key": "intraday", "state": "新鲜"},
        ],
    }


def test_load_plan_missing_or_broken(out_dir):
    assert prewarm.load_plan() == {}
    prewarm.plan_path().parent.mkdir(parents=True, exist_ok=True)
    prewarm.plan_path().write_text("{not json", encoding="utf-8")
    assert prewarm.load_plan() == {}


def test_load_plan_filters_non_api_paths(out_dir):
    _write_plan(out_dir, [{"path": "/api/ok"}, {"path": "/etc/passwd"}, {"x": 1}])
    plan = prewarm.load_plan()
    assert [t["path"] for t in plan["tasks"]] == ["/api/ok"]


def test_readiness_ok(out_dir):
    _write_plan(out_dir, [{"path": "/api/x"}], sources=("amv_board", "intraday"))
    ok, why = prewarm.readiness(_board(), prewarm.load_plan(), "20261106")
    assert ok is True and why == ""


def test_readiness_rejects_old_board_date(out_dir):
    _write_plan(out_dir, [{"path": "/api/x"}], sources=("amv_board",))
    ok, why = prewarm.readiness(_board(trade_date="2026-11-05"), prewarm.load_plan(), "20261106")
    assert ok is False and "20261105" in why


def test_readiness_rejects_stale_source(out_dir):
    _write_plan(out_dir, [{"path": "/api/x"}], sources=("amv_board", "track_pool"))
    board = _board(freshness=[{"key": "amv_board", "state": "新鲜"},
                              {"key": "track_pool", "state": "陈旧"}])
    ok, why = prewarm.readiness(board, prewarm.load_plan(), "20261106")
    assert ok is False and "track_pool" in why


def test_tasks_to_run_all_when_date_changes(out_dir):
    _write_plan(out_dir, [{"path": "/api/a"}, {"path": "/api/b"}])
    plan = prewarm.load_plan()
    state = {"expected": "20261105", "done": {"/api/a": "x"}, "failed": {}}
    assert [t["path"] for t in prewarm.tasks_to_run(state, plan, "20261106")] == ["/api/a", "/api/b"]


def test_tasks_to_run_skips_done_and_backs_off(out_dir):
    _write_plan(out_dir, [{"path": "/api/a"}, {"path": "/api/b"}, {"path": "/api/c"}])
    plan = prewarm.load_plan()
    now = time.time()
    state = {
        "expected": "20261106",
        "pid": prewarm._BOOT_PID,
        "done": {"/api/a": "x"},
        "failed": {"/api/b": {"count": 1, "next_at": now + 1000},
                   "/api/c": {"count": prewarm.MAX_RETRY, "next_at": 0}},
    }
    assert prewarm.tasks_to_run(state, plan, "20261106", now_ts=now) == []


def test_tasks_to_run_all_after_restart(out_dir):
    """不同进程（pid 不同）→ 旧 done 不可信，全量重跑（内存缓存已清空）。"""
    _write_plan(out_dir, [{"path": "/api/a"}])
    plan = prewarm.load_plan()
    state = {"expected": "20261106", "pid": prewarm._BOOT_PID + 1, "done": {"/api/a": "x"}}
    assert [t["path"] for t in prewarm.tasks_to_run(state, plan, "20261106")] == ["/api/a"]


def test_tasks_to_run_retries_after_backoff(out_dir):
    _write_plan(out_dir, [{"path": "/api/b"}])
    plan = prewarm.load_plan()
    now = time.time()
    state = {"expected": "20261106", "pid": prewarm._BOOT_PID, "done": {},
             "failed": {"/api/b": {"count": 1, "next_at": now - 1}}}
    assert [t["path"] for t in prewarm.tasks_to_run(state, plan, "20261106", now_ts=now)] == ["/api/b"]


def test_state_roundtrip(out_dir):
    prewarm._save_state({"expected": "20261106", "done": {"/api/a": "t"}})
    assert prewarm._load_state()["expected"] == "20261106"


def test_status_snapshot(out_dir):
    _write_plan(out_dir, [{"path": "/api/a"}], sources=("amv_board",))
    prewarm._save_state({"expected": "20261106"})
    s = prewarm.status_snapshot()
    assert s["plan_tasks"] == 1 and s["ready_sources"] == ["amv_board"]
    assert s["state"]["expected"] == "20261106"
    assert "expected_data_date" in s["trade_clock"]


def test_expand_codes_from_panel(out_dir, tmp_path, monkeypatch):
    """清单里的 `codes_from` 从 panel JSON 取代码（过滤条件写在清单，core 不含业务常量）。"""
    panel_dir = tmp_path / "panel"
    panel_dir.mkdir(parents=True, exist_ok=True)
    (panel_dir / "watchlist.json").write_text(json.dumps({
        "items": [
            {"kind": "stock", "code": "600519.SH"},
            {"kind": "sector", "code": "881101.TI"},
            {"kind": "stock", "code": "000001.SZ"},
        ]
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(paths, "panel_path", lambda name: panel_dir / (name + ".json"))
    task = {"path": "/api/x?codes={codes}",
            "codes_from": {"panel": "watchlist", "items": "items", "key": "code",
                           "where": {"kind": "stock"}, "max": 60}}
    assert prewarm._expand_codes(task) == ["600519.SH", "000001.SZ"]
    prepared = prewarm._prepare([task])
    assert prepared[0]["id"] == task["path"]
    assert "codes=600519.SH,000001.SZ" in prepared[0]["path"]


def test_expand_codes_missing_panel_skips_task(out_dir, monkeypatch):
    monkeypatch.setattr(paths, "panel_path", lambda name: None)
    task = {"path": "/api/x?codes={codes}", "codes_from": {"panel": "watchlist"}}
    assert prewarm._expand_codes(task) == []
    assert prewarm._prepare([task]) == []
