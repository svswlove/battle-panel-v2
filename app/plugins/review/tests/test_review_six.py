# -*- coding: utf-8 -*-
"""复盘六格（review 卡第四视图）测试（2026-11-03 建）。

覆盖：
1. review manifest：六格是**视图**不是新卡（cards 不变）、无新增数据权限；
2. 端点 `/api/review_six.json`：latest / 按日 / 404 / 400 / 500（源日期不自洽）；
3. core 读取器：available_dates / 源日期校验 / 缺文件 / 非法日期；
4. 生成器纯函数：六格筛选与排序、HHI、卡片字段（编号/标题/chips/依据/动作 pending）。

真实断言只读 `data/panel/review_six_cells_*`（生产只读）；临时用例全部落 tmp_path。
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from app.core import review_six as rs  # noqa: E402
from app.core.contract import load_manifest  # noqa: E402
from app.core.main import app  # noqa: E402
import review_six_build as builder  # noqa: E402

PLUGIN_DIR = Path(__file__).resolve().parents[1]     # app/plugins/review
FRONTEND = (PLUGIN_DIR / "frontend.js").read_text(encoding="utf-8")


def _doc(date: str = "2026-11-02", **over) -> Dict[str, Any]:
    base = {
        "schema_version": "1.0.0",
        "kind": "review_six_cells",
        "meta": {
            "target_date": date,
            "generated_at": "2026-11-02 17:00:00",
            "sources": {"sector_temp": {"date": date},
                        "sentiment": {"date": date.replace("-", "")}},
            "caliber": {}, "checks": {}, "warnings": [],
        },
        "headline": "今日 AMV 多、闸门 70%",
        "temperature": {"amv": {"value": 1.0}},
        "cells": [{"key": "x", "chips": [], "rows": [], "matched": [], "action": None}],
        "verdicts": {"temperature": {"conclusion": None}},
    }
    base.update(over)
    return base


def _write(directory: Path, date: str, doc: Dict[str, Any]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / f"review_six_cells_{date.replace('-', '')}.json"
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return p


# ---------------------------------------------------------------- 1. manifest / 前端
def test_manifest_has_no_new_card():
    """六格是 review 卡的第四个视图，不是新插件/新卡；也不新增数据权限。"""
    mf = load_manifest(PLUGIN_DIR)
    assert mf.id == "review"
    assert [c.id for c in mf.frontend.cards] == ["review.main"]
    assert mf.panel_read == () and mf.data_read == ()


def test_frontend_has_six_view():
    assert "['six', '六格']" in FRONTEND
    assert "renderSix" in FRONTEND
    assert "six: ['six']" in FRONTEND
    assert "/api/review_six.json" in FRONTEND
    # 卡片式版式字段
    for token in ("rv-six-card", "rv-six-chips", "rv-six-basis", "rv-six-act", "data-key"):
        assert token in FRONTEND, token


# ---------------------------------------------------------------- 2. 端点
def test_endpoint_ok_404_400(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "panel_dir", lambda: tmp_path)
    _write(tmp_path, "2026-11-02", _doc())
    (tmp_path / "review_six_cells_latest.json").write_text(
        json.dumps(_doc(), ensure_ascii=False), encoding="utf-8")
    client = TestClient(app)

    r = client.get("/api/review_six.json")
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["target_date"] == "2026-11-02"
    assert body["available_dates"] == ["2026-11-02"]

    r2 = client.get("/api/review_six.json?date=2026-11-02")
    assert r2.status_code == 200 and r2.json()["artifact"] == "2026-11-02"

    assert client.get("/api/review_six.json?date=2026-11-03").status_code == 404
    assert client.get("/api/review_six.json?date=xx").status_code == 400


def test_endpoint_mismatch_is_500(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "panel_dir", lambda: tmp_path)
    doc = _doc()
    doc["meta"]["sources"]["sentiment"]["date"] = "20261030"
    _write(tmp_path, "2026-11-02", doc)
    (tmp_path / "review_six_cells_latest.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    assert TestClient(app).get("/api/review_six.json").status_code == 500


# ---------------------------------------------------------------- 3. 读取器
def test_reader_available_and_latest(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "panel_dir", lambda: tmp_path)
    _write(tmp_path, "2026-10-30", _doc("2026-10-30"))
    _write(tmp_path, "2026-11-02", _doc("2026-11-02"))
    (tmp_path / "review_six_cells_latest.json").write_text(
        json.dumps(_doc("2026-11-02"), ensure_ascii=False), encoding="utf-8")
    assert rs.available_dates() == ["2026-10-30", "2026-11-02"]
    assert rs.load()["artifact"] == "latest"
    assert rs.load("20261030")["meta"]["target_date"] == "2026-10-30"


def test_reader_rejects_mismatch_and_bad_date(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "panel_dir", lambda: tmp_path)
    doc = _doc()
    doc["meta"]["sources"]["sector_temp"]["date"] = "2026-10-30"
    _write(tmp_path, "2026-11-02", doc)
    with pytest.raises(rs.ReviewSixError):
        rs.load("2026-11-02")
    with pytest.raises(ValueError):
        rs.load("2026-13-40")
    with pytest.raises(rs.ReviewSixError):
        rs.load("2026-11-05")


# ---------------------------------------------------------------- 4. 生成器纯函数
def _board(code: str, name: str, pct: float, state: str) -> Dict[str, Any]:
    return {"code": code, "name": name, "level": "L2", "pct": pct, "state": state}


def _sd(share: float, d1: float, d5: float) -> Dict[str, Any]:
    return {"share": share, "d1": d1, "d5": d5, "amv": 1e10, "name": "x"}


def test_build_cells_filters_order_and_card_fields():
    boards = [
        _board("A", "甲", 2.0, "多"),
        _board("B", "乙", 0.5, "多"),
        _board("C", "丙", 1.5, "空"),
        _board("D", "丁", -1.0, "空"),
    ]
    sd = {"A": _sd(0.1, 0.001, 0.003), "B": _sd(0.2, 0.0005, 0.001),
          "C": _sd(0.3, -0.00002, -0.001), "D": _sd(0.4, -0.001, -0.002)}
    cnt = Counter({"A": 1, "C": 2})
    items = [{"continue_day_cnt": 1}, {"continue_day_cnt": 1}, {"continue_day_cnt": 2}]
    cells = builder.build_cells(boards, sd, cnt, items, 3,
                                {"A": "甲", "B": "乙", "C": "丙", "D": "丁"},
                                streak_map={"A": 2})

    c1, c2, c3, c4, c5, c6 = cells
    assert [r["name"] for r in c1["rows"]] == ["甲", "乙"]
    # D：冷门背离排除已进持续吸金的板块（乙 在 c1 里）
    assert [r["name"] for r in c2["rows"]] == []
    # B：强共振 state=多 在前；丙(state=空) 排后面
    assert [r["name"] for r in c3["rows"]] == ["甲"]
    assert c3["rows"][0].get("struct_note") is None
    assert [r["name"] for r in c4["rows"]] == ["丁"]
    # C：杠杆背离下限（丙 pct 1.5% 但 |d1| 0.002pp < 0.02pp → 不进格）
    assert c5["rows"] == [] and c5["n_matched"] == 0
    assert c5["basis"].startswith("今日无")
    assert c6["hhi"] == 0.5556

    # 卡片式版式字段（规格 v2）
    assert [c["no"] for c in cells] == ["01", "02", "03", "04", "05", "06"]
    assert c1["title"] == "重点配置 · 持续吸金"
    assert c2["title"] == "潜伏观察 · 冷门流入"
    assert c3["title"] == "趋势确认 · 强共振"
    assert c4["title"] == "风险最高 · 共振流出"
    assert c5["title"] == "分歧提示 · 杠杆背离"
    assert c6["title"] == "情绪焦点 · 涨停集中"
    assert c1["chips"] and c1["chips"][0]["metric"].startswith("d5 ")
    assert "连续 2 天" in c1["basis"]
    assert set(c1["chips"][0]) >= {"name", "metric", "metric_key", "value", "state"}
    assert all(c["action"] is None and c["action_status"] == "pending" for c in cells)
    assert c5["empty_note"] == "今日无"


def test_strong_resonance_puts_bull_state_first():
    """B：state=空 的板块即使 d1 更大，也不能排第一；要标「结构未转多」。"""
    boards = [_board("A", "甲", 2.0, "空"), _board("B", "乙", 1.0, "多")]
    sd = {"A": _sd(0.1, 0.5, 0.001), "B": _sd(0.2, 0.05, 0.001)}
    cnt = Counter({"A": 3, "B": 1})
    items = [{"continue_day_cnt": 1}] * 4
    cells = builder.build_cells(boards, sd, cnt, items, 4, {"A": "甲", "B": "乙"})
    c3 = cells[2]
    assert [r["name"] for r in c3["rows"]] == ["乙", "甲"]
    assert c3["rows"][1].get("struct_note") == "结构未转多"
    assert "结构未转多" not in (c3["basis"] or "")


def test_leverage_lower_bound_and_empty_note():
    """C：pct<1% 或 |d1|<0.02pp 不进格；全无合格标的 → 今日无。"""
    boards = [_board("A", "甲", 0.8, "多"), _board("B", "乙", 1.2, "多"),
              _board("C", "丙", 1.5, "多")]
    sd = {"A": _sd(0.1, -0.05, 0.001), "B": _sd(0.2, -0.00001, 0.001),
          "C": _sd(0.3, -0.03, 0.001)}
    cells = builder.build_cells(boards, sd, Counter(), [], 0, {"A": "甲", "B": "乙", "C": "丙"})
    c5 = cells[4]
    # 甲 pct 0.8% 不够；乙 |d1| 0.001pp 不够；丙 pct 1.5%、d1 -3pp 合格
    assert [r["name"] for r in c5["rows"]] == ["丙"]
    # 全部不合格 → 今日无
    cells2 = builder.build_cells([_board("A", "甲", 0.8, "多")],
                                 {"A": _sd(0.1, -0.001, 0.001)}, Counter(), [], 0, {"A": "甲"})
    assert cells2[4]["rows"] == [] and cells2[4]["basis"].startswith("今日无")


def test_build_headline_mentions_ma10_position():
    """A：headline 不许只写「AMV 多」；低于 MA10 时必须带差额 + 单轨未确认。"""
    temp = {"state": "多", "amv": {"value": 134985.66}, "ma10": {"value": 136065.45},
            "excess": {"value": -1079.79, "pct": -0.7936},
            "amv_vs_ma10": {"position": "below", "unconfirmed": True, "label": "单轨未确认"},
            "gate": {"pct": 55}, "breadth": {"ratio_pct": 42.88},
            "limitup": {"zt": 52}, "switch": {"date": "2026-11-02", "dir": "多", "prev_dir": "空"}}
    line = builder.build_headline("2026-11-03", temp, {})
    assert "低于 MA10 1,080 亿" in line
    assert "单轨未确认" in line
    assert "state=多" in line and "55%" in line


def test_build_headline_is_numeric():
    temp = {"state": "多", "gate": {"pct": 70}, "breadth": {"ratio_pct": 42.99},
            "limitup": {"zt": 131}, "switch": {"date": "2026-11-02", "dir": "多"}}
    regime = {"recent_switches": [{"dir": "空"}, {"dir": "多"}]}
    line = builder.build_headline("2026-11-02", temp, regime)
    assert "state=多" in line and "空转多" in line and "70%" in line and "131" in line
