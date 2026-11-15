# -*- coding: utf-8 -*-
"""个股打分卡契约测试（2026-10-29）。

守的是这一轮的契约：**产物唯一源 = 打分引擎脚本，插件只读托管，且托管在插件内**。
  · 产物唯一源 = `projects/panel-v2/data/panel_v2/score/latest.json`（stock_score_engine.py 落盘）；
  · `/meta` 只做 stat + 一次解析，不重算任何分数；`/rows` 原样返回字节级内容；
  · 产物缺失 → `/rows` 503（面板显示「还没生成」），不是 500、不是空数组；
  · 插件不直连数据库（源码里不得出现 sqlite3），`data.write` 为空；
  · 拆卸 = 删插件目录 + 删 shell.js NAV 一行，core 不需要认识它。
不跑浏览器；用 TestClient 验真实运行。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import paths  # noqa: E402
from app.core.contract import load_manifest  # noqa: E402
from app.core.main import create_app  # noqa: E402
from app.plugins.score import backend as score  # noqa: E402

PLUGIN_DIR = Path(__file__).resolve().parents[1]
FRONTEND = PLUGIN_DIR / "frontend.js"
BACKEND = PLUGIN_DIR / "backend.py"
SHELL = ROOT / "app" / "web" / "shell.js"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _fake_payload() -> dict:
    return {
        "meta": {
            "generated_at": "2026-10-29 22:35:07",
            "asof": "2026-10-29",
            "coverage": {
                "universe": 2,
                "scored": 2,
                "dims": {"fund": 2, "sector": 2, "trend": 1, "volprice": 1, "value": 1, "quality": 2},
                "subs": {"fund_w1": 2},
                "reweight": {"stocks_with_missing_dim": 1, "by_n_dims": {"3": 1, "6": 1},
                             "missing_dim_counts": {"trend": 1}},
            },
            "distribution": {"p10": 10.0, "p50": 50.0, "p90": 90.0},
            "grades": {"S": 1, "D": 1},
            "grade_cutoffs": [70.0, 57.0, 40.0, 28.0],
            "grade_labels": ["S", "A", "B", "C", "D"],
            "weights": {"fund": 0.3, "sector": 0.2, "trend": 0.2,
                        "volprice": 0.15, "value": 0.1, "quality": 0.05},
            "sources": {"fund": "amv_board.db"},
            "caliber": "六维加权",
        },
        "rows": [
            {"code6": "600519", "name": "贵州茅台", "score": 90.0, "grade": "S", "rank": 1,
             "score_in_sector": 88.0, "rank_in_sector": 1, "n_dims": 6,
             "sector_code": "881273.TI", "sector_name": "白酒", "dim_fund": 99.0},
            {"code6": "000001", "name": "平安银行", "score": 10.0, "grade": "D", "rank": 2,
             "score_in_sector": 9.0, "rank_in_sector": 1, "n_dims": 3,
             "sector_code": "881155.TI", "sector_name": "银行", "dim_fund": None},
        ],
    }


@pytest.fixture()
def fake_out(tmp_path, monkeypatch):
    out = tmp_path / "panel_v2"
    (out / "score").mkdir(parents=True)
    monkeypatch.setattr(paths, "OUT_DIR", out)
    return out


# ---------------------------------------------------------------- manifest / 路由


def test_manifest_loads_and_declares_card():
    m = load_manifest(PLUGIN_DIR)
    assert m.id == "score"
    assert m.enabled is True
    assert [c["id"] for c in json.loads(_read(PLUGIN_DIR / "plugin.json"))["frontend"]["cards"]] == ["score.main"]
    # 插件不写任何生产文件、不读任何白名单库（只读自己的产物）
    raw = json.loads(_read(PLUGIN_DIR / "plugin.json"))
    assert raw["data"]["write"] == []
    assert raw["data"]["read"] == []
    assert raw["data"]["panel"] == []


def test_plugin_does_not_touch_database():
    src = _read(BACKEND)
    for bad in ("sqlite3", "psycopg", "pymysql"):
        assert bad not in src, "插件不得直连数据库（发现 %s）" % bad


def test_frontend_contract():
    src = _read(FRONTEND)
    assert "export default" in src
    assert "score.main" in src
    assert not src.lstrip().startswith("import "), "前端必须是零 import 的原生 ES module"
    # 点代码开 K 线：靠 shell 的 data-open-kline 委托，卡内不复刻打开逻辑
    assert "data-open-kline" in src


def test_shell_nav_has_one_line_for_score():
    src = _read(SHELL)
    assert "score::score.main" in src
    assert src.count("score::score.main") == 1, "NAV 只应有一行，拆卸时删这一行即可"


# ---------------------------------------------------------------- 端点


def test_meta_when_product_missing(fake_out):
    client = TestClient(create_app())
    r = client.get("/api/p/score/meta")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["available"] is False
    assert "stock_score_engine.py" in body["reason"]


def test_rows_when_product_missing_is_503(fake_out):
    client = TestClient(create_app())
    r = client.get("/api/p/score/rows")
    assert r.status_code == 503
    assert r.json()["error"] == "score product missing"


def test_meta_and_rows_passthrough(fake_out):
    payload = _fake_payload()
    (fake_out / "score" / "latest.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    client = TestClient(create_app())

    meta = client.get("/api/p/score/meta").json()
    assert meta["available"] is True
    assert meta["asof"] == "2026-10-29"
    assert meta["n_rows"] == 2
    assert meta["coverage"]["universe"] == 2
    assert meta["grade_cutoffs"] == [70.0, 57.0, 40.0, 28.0]
    assert meta["stale"] is False

    rows = client.get("/api/p/score/rows").json()
    assert rows == payload, "后端不得改写产物（排序/筛选是前端的事）"


def test_broken_product_reports_unavailable(fake_out):
    (fake_out / "score" / "latest.json").write_text("{not json", encoding="utf-8")
    client = TestClient(create_app())
    body = client.get("/api/p/score/meta").json()
    assert body["available"] is False
    assert "解析失败" in body["reason"]


# ---------------------------------------------------------------- /map（三个列表页的取数口）


def test_map_projects_flat_fields(fake_out):
    """`/map` 只投影 score/grade/rank，字段顺序由 `fields` 显式声明（前端不靠位置猜）。"""
    (fake_out / "score" / "latest.json").write_text(
        json.dumps(_fake_payload(), ensure_ascii=False), encoding="utf-8")
    body = TestClient(create_app()).get("/api/p/score/map").json()
    assert body["ok"] is True and body["available"] is True
    assert body["fields"] == ["score", "grade", "rank"]
    assert body["asof"] == "2026-10-29"
    assert body["n"] == 2
    assert body["map"]["600519"] == [90.0, "S", 1]
    assert body["map"]["000001"] == [10.0, "D", 2]
    # 不带分项、不带板块信息（省体积；要分项走 /rows）
    assert all(len(v) == 3 for v in body["map"].values())


def test_map_rounds_score_to_one_decimal(fake_out):
    payload = _fake_payload()
    payload["rows"][0]["score"] = 90.4567
    (fake_out / "score" / "latest.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    body = TestClient(create_app()).get("/api/p/score/map").json()
    assert body["map"]["600519"][0] == 90.5


def test_map_when_product_missing_is_200_available_false(fake_out):
    """缺产物不报错：消费方是别的插件的列表页，只该让那一列变「—」。"""
    body = TestClient(create_app()).get("/api/p/score/map").json()
    assert body["ok"] is True and body["available"] is False
    assert body["map"] == {}
    assert "stock_score_engine.py" in body["reason"]


def test_map_stays_under_200kb_on_full_universe(fake_out):
    """满池 5577 只：整份响应必须 < 200KB（任务书约束；数组字段顺序为此选）。"""
    rows = []
    for i in range(5577):
        rows.append({"code6": "%06d" % (600000 + i), "name": "股%d" % i,
                     "score": 50.0 + (i % 400) / 10.0, "grade": "ABCD"[i % 4],
                     "rank": i + 1, "dim_fund": 1.0, "sector_name": "板块"})
    (fake_out / "score" / "latest.json").write_text(
        json.dumps({"meta": {"asof": "2026-10-29"}, "rows": rows}, ensure_ascii=False),
        encoding="utf-8")
    body = TestClient(create_app()).get("/api/p/score/map").json()
    assert body["n"] == 5577
    size = len(json.dumps(body, ensure_ascii=False).encode("utf-8"))
    assert size < 200 * 1024, "map 体积 %d 字节超 200KB —— 字段又变宽了？" % size


def test_map_picks_up_new_product(fake_out, monkeypatch):
    """产物重跑后 `/map` 必须重新读盘（不能把旧缓存当时的新数据）。"""
    path = fake_out / "score" / "latest.json"
    path.write_text(json.dumps(_fake_payload(), ensure_ascii=False), encoding="utf-8")
    client = TestClient(create_app())
    assert client.get("/api/p/score/map").json()["n"] == 2

    payload = _fake_payload()
    payload["rows"].append({"code6": "300750", "name": "宁德时代", "score": 66.0,
                            "grade": "A", "rank": 3})
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    # 显式推 mtime（有些文件系统 mtime 精度只到秒，靠 write 自己变不够）
    stamp = datetime.now().timestamp() + 30
    os.utime(path, (stamp, stamp))
    assert client.get("/api/p/score/map").json()["n"] == 3


def test_plugin_health_ok():
    client = TestClient(create_app())
    health = client.get("/_plugins/health").json()
    rec = [p for p in health["plugins"] if p["id"] == "score"]
    assert rec and rec[0]["ok"] is True
