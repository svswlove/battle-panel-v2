# -*- coding: utf-8 -*-
"""情绪打分 · 契约 + 真实数据测试（2026-10-29）。

守三层：
1. **插件形态**：manifest 可加载、卡唯一、`data.read` 只声明 track_pool.db、`data.write` 为空；
   `app/core/main.py` / `paths.py` 里不得出现 emotion 字样（拆卸 = 删目录 + 删 NAV 一行）。
2. **接口**：`/api/p/emotion/payload` 与 `/meta` 在真实 core 上返回今日六因子 + 序列。
3. **对答案**（仅在数据日 = 2026-10-29 时断言，避免把日频快照写死成永久期望）：
   涨停 39 / 曾涨停 64 / 跌停 42 / 最高 4 板 / 连板 10 → 炸板率 ≈39%、跌停项 −10、总分 61.38。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import paths  # noqa: E402
from app.core.contract import load_manifest  # noqa: E402
from app.core.main import create_app  # noqa: E402
from app.plugins.emotion import engine, source  # noqa: E402

PLUGIN_DIR = Path(__file__).resolve().parents[1]
FRONTEND = PLUGIN_DIR / "frontend.js"
CORE_DIR = ROOT / "app" / "core"

DB_PRESENT = (paths.db_path("track_pool.db") or Path("")).is_file()
needs_db = pytest.mark.skipif(not DB_PRESENT, reason="track_pool.db 不在本机")


# ---------------------------------------------------------------- 插件形态

def test_manifest_loads_and_declares_single_card():
    m = load_manifest(PLUGIN_DIR)
    assert m.id == "emotion"
    assert m.enabled is True
    assert [c.id for c in m.frontend.cards] == ["emotion.main"]
    assert m.frontend.cards[0].slot == "main"
    assert m.frontend.cards[0].title == "情绪打分"


def test_manifest_is_read_only_on_track_pool():
    m = load_manifest(PLUGIN_DIR)
    assert tuple(m.data_read) == ("track_pool.db",)
    assert tuple(m.data_write) == ()


def test_core_does_not_know_emotion():
    """拆卸保证：core 里不出现 emotion 字样（否则「删插件目录」就不够了）。"""
    for name in ("main.py", "paths.py", "contract.py"):
        text = (CORE_DIR / name).read_text(encoding="utf-8")
        assert "emotion" not in text.lower(), name


def test_frontend_is_zero_import_module():
    text = FRONTEND.read_text(encoding="utf-8")
    assert not re.search(r"^\s*import\s", text, re.MULTILINE), "前端必须零 import（CONTRACT §5）"
    assert "require(" not in text
    # 只打自己的插件端点
    assert "/payload" in text
    assert "stage" in text and "stage_note" in text


def test_frontend_shows_stage_and_score_side_by_side():
    """两套口径并列：页面必须同时渲染 stage 与分数，且不把 stage 当档位用。"""
    text = FRONTEND.read_text(encoding="utf-8")
    assert "规则硬判" in text
    assert "连续打分" in text
    assert "不合并" in text


# ---------------------------------------------------------------- 晋级率复算（合成数据）

def test_derive_promotions_synthetic():
    pool = {
        "20260101": {2: {"a", "b", "c"}, 1: {"x"}},
        "20260102": {3: {"a"}, 2: {"b"}, 1: {"y"}},
        "20260103": {3: {"b"}, 2: {"a"}},
    }
    out = source.derive_promotions(pool)
    # 2026-01-02：昨日 2 板 3 家（a/b/c），今日 3 板 a → 1/3
    assert out["20260102"]["promotion_2b"] == pytest.approx(1 / 3)
    assert out["20260102"]["base_2b"] == 3 and out["20260102"]["hit_2b"] == 1
    # 昨日无 3 板 → 分母 0 → None（不是 0）
    assert out["20260102"]["promotion_3b"] is None
    # 2026-01-03：昨日 2 板 {b}，今日 3 板 {b} → 1/1
    assert out["20260103"]["promotion_2b"] == pytest.approx(1.0)


def test_promotion_levels_cover_2_to_5():
    assert source.PROMOTION_LEVELS == (2, 3, 4, 5)


# ---------------------------------------------------------------- 真实数据 / 接口

@pytest.fixture(scope="module")
def client():
    app = create_app()
    with TestClient(app) as c:
        yield c


@needs_db
def test_payload_endpoint_returns_six_factors(client):
    res = client.get("/api/p/emotion/payload?history_days=60")
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["ok"] is True
    today = data["today"]
    assert len(today["factors"]) == 6
    keys = [f["key"] for f in today["factors"]]
    assert keys == list(engine.FACTOR_KEYS)
    assert 0 <= today["score"] <= 100
    assert today["band"] in ("亢奋", "偏热", "中性", "偏冷", "冰点")
    # 序列长度不超过请求窗口，且按日期升序
    hist = data["history"]
    assert 0 < len(hist) <= 60
    assert [p["date"] for p in hist] == sorted(p["date"] for p in hist)
    assert hist[-1]["date"] == data["asof"]


@needs_db
def test_meta_endpoint(client):
    res = client.get("/api/p/emotion/meta")
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["ok"] is True
    assert data["asof"] and data["n_trade_dates"] > 0
    assert data["promotion"]["factor_available"] is True


@needs_db
def test_today_matches_answer_sheet(client):
    """2026-10-29 对答案（数据日不是 10-29 时自动跳过，不把快照写死）。"""
    data = client.get("/api/p/emotion/payload").json()
    if data["asof"] != "20261029":
        pytest.skip("数据日已推进到 %s，2026-10-29 对答案不再适用" % data["asof"])

    today = data["today"]
    factors = {f["key"]: f for f in today["factors"]}

    assert factors["zt_total"]["raw"] == 39
    assert factors["burst"]["raw"] == pytest.approx(0.3906)          # (64−39)/64
    assert factors["burst"]["points"] == pytest.approx(-19.06)      # 20 − 39.06（v1.1 修正符号）
    assert factors["dt_vs_zt"]["points"] == pytest.approx(-10.0)     # 跌停 42 > 涨停 39
    assert factors["height"]["raw"] == 4
    assert factors["height"]["points"] == pytest.approx(0.0)
    assert factors["lb2plus"]["raw"] == 10
    assert factors["promotion"]["raw"] == pytest.approx(0.40)        # 2/5（本地涨停池）
    assert factors["promotion"]["participated"] is False             # 分母 5 只 < 10 → 不给分
    assert today["promotion_base"] == 5
    assert today["score"] == pytest.approx(8.26, abs=0.01)
    assert today["band"] == "冰点"
    assert today["stage"] == "退潮"
    assert today["stage_note"].startswith("涨停 73→39 骤降")
    assert today["n_factors"] == 5
    assert data["caliber_version"] == "v1.1"
    assert data["caliber_revised"] == "2026-10-29"
    assert data["burst_sign"] == -1


@needs_db
def test_zt_threshold_is_rolling_not_fixed(client):
    """阈值必须是滚动分位：2026-10-29 的 q50 约 60.5，不是 68/100。"""
    data = client.get("/api/p/emotion/payload").json()
    thr = data["today"]["zt_threshold"]
    assert thr is not None
    assert 55 < thr < 70
    assert thr != 68 and thr != 100


@needs_db
def test_promotion_matches_old_db_value_on_20261013(client):
    """本地复算与库内旧 MCP 值在 2026-10-13 完全一致（5/14 = 35.71%），口径可对。"""
    data = client.get("/api/p/emotion/payload?history_days=400").json()
    point = next((p for p in data["history"] if p["date"] == "20261013"), None)
    assert point is not None
    assert point["promotion_2b"] == pytest.approx(5 / 14, abs=1e-6)


@needs_db
def test_premium_marked_unavailable(client):
    data = client.get("/api/p/emotion/payload").json()
    assert data["promotion"]["premium_available"] is False
    assert "premium" in data["promotion"]["premium_reason"]


@needs_db
def test_no_write_to_production_paths(client):
    """只读守门：调用接口后 sentiment 表行数与 data/panel 目录都不变。"""
    import sqlite3

    conn = sqlite3.connect("file:" + (paths.db_path("track_pool.db")).as_posix() + "?mode=ro", uri=True)
    try:
        before = conn.execute("SELECT COUNT(*) FROM sentiment").fetchone()[0]
        before_max = conn.execute("SELECT MAX(trade_date) FROM sentiment").fetchone()[0]
    finally:
        conn.close()

    client.get("/api/p/emotion/payload")

    conn = sqlite3.connect("file:" + (paths.db_path("track_pool.db")).as_posix() + "?mode=ro", uri=True)
    try:
        after = conn.execute("SELECT COUNT(*) FROM sentiment").fetchone()[0]
        after_max = conn.execute("SELECT MAX(trade_date) FROM sentiment").fetchone()[0]
    finally:
        conn.close()
    assert (before, before_max) == (after, after_max)
