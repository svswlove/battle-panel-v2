# -*- coding: utf-8 -*-
"""预警页 8810 API（2026-11-07 预警页重建任务书 §二/§六）。

跑法：cd projects/panel-v2 && python -m pytest app/tests/test_alert_page.py -q
边界：临时存储 + 假 ctx，不读写生产 data/panel、不发推送。

守的规则：
  · 写只走 alert_store（唯一写入口）；条目结构只由 add_alert 生成
  · 只提醒不下单：插件不得 import 下单链
  · 出口互斥：系统级类型只能 L1，个股级只能 L2
  · 手工开关只改 enabled 通道（→ CLOSED），不删记录
"""
import ast
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.plugins.alert import backend as B  # noqa: E402
from app.plugins.alert import backend as _  # noqa: F401


def _endpoint(path, method="POST"):
    router = B.register(None)
    for r in router.routes:
        if getattr(r, "path", None) == path and method in getattr(r, "methods", set()):
            return r.endpoint
    raise AssertionError("路由不存在：%s %s" % (method, path))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    store = tmp_path / "alert_plan_persistent.json"
    flow = tmp_path / "alert_flow.jsonl"
    quotes = tmp_path / "watchlist_quotes.json"
    quotes.write_text(json.dumps({"quotes": {"600271.SH": {"price": 7.27}}}), encoding="utf-8")
    monkeypatch.setattr(B.STORE, "STORE_FILE", str(store))
    monkeypatch.setattr(B.STORE, "FLOW_FILE", str(flow))
    monkeypatch.setattr(B, "QUOTES", str(quotes))
    monkeypatch.setattr(B, "FOCUS", str(tmp_path / "focus_levels.json"))
    monkeypatch.setattr(B, "INTRADAY", str(tmp_path / "intraday.json"))
    monkeypatch.setattr(B, "ALERT_TODO", str(tmp_path / "alert_todo.json"))
    monkeypatch.setattr(B, "PERSISTENT", str(store))
    monkeypatch.setattr(B.AA, "BP_FILE", str(tmp_path / "battle_plan.json"))
    (tmp_path / "battle_plan.json").write_text(json.dumps({"operations": [], "watch_levels": []}),
                                               encoding="utf-8")
    return {"dir": tmp_path, "store": store, "flow": flow}


def _doc(env):
    if not env["store"].exists():
        return {"plans": []}
    return json.loads(env["store"].read_text(encoding="utf-8"))


def test_add_price_alert_writes_v2_entry(env):
    r = _endpoint("/alert/write")({"action": "add", "type": "PRICE_BELOW", "code": "600271.SH",
                                  "name": "航天信息", "value": 7.0, "source": "kline"})
    assert r["ok"] and r["added"] is True
    e = _doc(env)["plans"][0]
    assert e["exit"] == "L2" and e["source"] == "kline" and e["state"] == "PENDING"
    assert e["enabled"] is True and e["condition"]["terms"][0]["value"] == 7.0
    assert e["severity"] == "notify"


def test_list_shape_has_enums_and_flow(env):
    _endpoint("/alert/write")({"action": "add", "type": "PRICE_BELOW", "code": "600271.SH", "value": 7.0})
    d = _endpoint("/alert/list", "GET")()
    assert d["ok"] and d["plans"] and d["plans"][0]["kind_cn"] == "跌破价"
    assert {x["code"] for x in d["enums"]["states"]} >= {"PENDING", "MONITORING", "PUSHED", "CLOSED"}
    assert {x["code"] for x in d["enums"]["exits"]} == {"L1", "L2"}
    assert len(d["enums"]["types"]) >= 10
    assert d["flow"] and d["flow"][0]["to"] == "PENDING"
    assert "todo" in d and "notify" in d
    # 卡片分组（2026-11-07 主人要的卡片式）：一只票一张卡 + 徽标文案 + 亮灭位
    assert d["groups"] and d["groups"][0]["code"] == "600271.SH"
    p = d["plans"][0]
    assert p["lit"] is True and p["name"] == "600271.SH"
    # 徽标 = 防线名 + 距离（2026-11-07 01:5x 主人：不要显示阀值数字）
    assert p["level_tag"] == "价位"          # 查不到同价防守线 → 叫「价位」
    assert p["badge"].startswith("价位")
    if "%" in p["badge"]:
        assert "+3.9%" in p["badge"]        # 7.27 vs 7.00 → +3.9%
        # 靠近防线：5% 内给外圈深浅（prox 0~1），1% 内 hot（主人 2026-11-07）
        assert 0 < p["prox"] < 1, "5% 内应该给外圈深浅"
        assert p["hot"] is False, "3.9% 还不是红线"
    assert "7.0" not in p["badge"], "徽标不得外露阀值数字"


def test_group_freeze_and_unfreeze(env):
    """整卡冻结：一只票的所有防线一次全关 → 卡片 frozen；恢复后回到 PENDING。"""
    w = _endpoint("/alert/write")
    for v in (7.0, 6.8):
        w({"action": "add", "type": "PRICE_BELOW", "code": "600271.SH", "value": v})
    assert w({"action": "toggle_group", "code": "600271.SH", "enabled": False})["ok"]
    d = _endpoint("/alert/list", "GET")()
    g = next(x for x in d["groups"] if x["code"] == "600271.SH")
    assert g["frozen"] is True and g["enabled"] == 0 and g["lit"] == 0
    assert all(p["state"] == "CLOSED" for p in g["plans"])
    assert w({"action": "toggle_group", "code": "600271.SH", "enabled": True})["ok"]
    g2 = next(x for x in _endpoint("/alert/list", "GET")( )["groups"] if x["code"] == "600271.SH")
    assert g2["frozen"] is False and g2["lit"] == 2


def test_duplicate_is_idempotent(env):
    w = _endpoint("/alert/write")
    w({"action": "add", "type": "PRICE_BELOW", "code": "600271.SH", "value": 7.0})
    r2 = w({"action": "add", "type": "PRICE_BELOW", "code": "600271.SH", "value": 7.0})
    assert r2["ok"] and r2.get("duplicate") is True
    assert len(_doc(env)["plans"]) == 1


def test_remove(env):
    w = _endpoint("/alert/write")
    dp = w({"action": "add", "type": "PRICE_BELOW", "code": "600271.SH", "value": 7.0})["dp_id"]
    assert w({"action": "remove", "dp_id": dp})["ok"] is True
    assert _doc(env)["plans"] == []
    assert w({"action": "remove", "dp_id": dp})["ok"] is False


def test_toggle_closes_without_deleting(env):
    w = _endpoint("/alert/write")
    dp = w({"action": "add", "type": "PRICE_BELOW", "code": "600271.SH", "value": 7.0})["dp_id"]
    r = w({"action": "toggle", "dp_id": dp, "enabled": False})
    assert r["ok"] and r["enabled"] is False
    plans = _doc(env)["plans"]
    assert len(plans) == 1 and plans[0]["state"] == "CLOSED" and plans[0]["enabled"] is False
    assert w({"action": "toggle", "dp_id": dp, "enabled": True})["ok"] is True
    assert _doc(env)["plans"][0]["state"] == "PENDING"


def test_channels_written_to_meta(env):
    w = _endpoint("/alert/write")
    r = w({"action": "channels", "channels": {"qq": False}})
    assert r["ok"] and r["channels"]["qq"] is False
    assert _doc(env).get("meta", {}).get("channels", {}).get("qq") is False


def test_l1_type_forces_wake_exit(env):
    w = _endpoint("/alert/write")
    r = w({"action": "add", "type": "INDEX_PCT_BELOW", "value": 2.5, "exit": "L2"})
    assert r["ok"] is False and "L1" in r["error"]
    r2 = w({"action": "add", "type": "INDEX_PCT_BELOW", "value": 2.5})
    assert r2["ok"] and r2["entry"]["exit"] == "L1"
    e = _doc(env)["plans"][0]
    assert e["severity"] == "alert" and e["condition"]["terms"][0]["metric"] == "index_pct"


def test_l2_type_rejects_l1(env):
    r = _endpoint("/alert/write")({"action": "add", "type": "PRICE_BELOW", "code": "600271.SH",
                                  "value": 7.0, "exit": "L1"})
    assert r["ok"] is False and "L2" in r["error"]


def test_dynamic_required_for_line_alerts(env):
    w = _endpoint("/alert/write")
    r = w({"action": "add", "type": "LINE_BELOW", "code": "600271.SH", "line": "white"})
    assert r["ok"] is False and "dynamic" in r["error"]
    r2 = w({"action": "add", "type": "LINE_BELOW", "code": "600271.SH", "line": "white", "dynamic": True})
    assert r2["ok"] and r2["added"]


def test_price_alert_requires_value(env):
    r = _endpoint("/alert/write")({"action": "add", "type": "PRICE_BELOW", "code": "600271.SH"})
    assert r["ok"] is False and "数值" in r["error"]


def test_default_order_by_nearest(env):
    """默认排序 = 离防线越近越前（主人 2026-11-07 04:1x 定）。"""
    (env["dir"] / "watchlist_quotes.json").write_text(json.dumps({"quotes": {
        "600271.SH": {"price": 7.27},   # 距 7.0 约 3.9%
        "600001.SH": {"price": 7.05},   # 0.7%
        "600002.SH": {"price": 9.00},   # 28.6%
    }}), encoding="utf-8")
    w = _endpoint("/alert/write")
    for code in ("600271.SH", "600001.SH", "600002.SH"):
        assert w({"action": "add", "type": "PRICE_BELOW", "code": code, "value": 7.0})["ok"]
    keys = [g["key"] for g in _endpoint("/alert/list", "GET")()["groups"]]
    assert keys == ["600001.SH", "600271.SH", "600002.SH"], keys


def test_reorder_and_frozen_keeps_position(env):
    """拖动排序落库；冻结一张卡不改变它的位置（主人 2026-11-07）。"""
    w = _endpoint("/alert/write")
    for code in ("600271.SH", "600001.SH", "600002.SH"):
        assert w({"action": "add", "type": "PRICE_BELOW", "code": code, "value": 7.0})["ok"]
    keys = [g["key"] for g in _endpoint("/alert/list", "GET")()["groups"]]
    assert len(keys) == 3
    assert w({"action": "reorder_cards", "order": list(reversed(keys))})["ok"]
    assert [g["key"] for g in _endpoint("/alert/list", "GET")()["groups"]] == list(reversed(keys))
    target = keys[0]
    assert w({"action": "toggle_group", "code": target, "exit": "", "enabled": False})["ok"]
    d = _endpoint("/alert/list", "GET")()
    assert [g["key"] for g in d["groups"]] == list(reversed(keys)), "冻结后位置变了"
    assert next(g for g in d["groups"] if g["key"] == target)["frozen"] is True


def test_unknown_action_rejected(env):
    r = _endpoint("/alert/write")({"action": "delete_all"})
    assert r["ok"] is False and "action" in r["error"]


def test_origin_manual_by_default_and_listed(env):
    """2026-11-11：面板新增一律 manual（会推送）；三值枚举由后端下发。"""
    r = _endpoint("/alert/write")({"action": "add", "type": "PRICE_BELOW",
                                   "code": "600271.SH", "value": 7.0})
    assert r["ok"] and r["origin"] == "manual"
    assert _doc(env)["plans"][0]["origin"] == "manual"
    d = _endpoint("/alert/list", "GET")()
    assert {x["code"] for x in d["enums"]["origins"]} == {"manual", "review", "auto"}
    assert set(d["enums"]["notify_origins"]) == {"manual", "review"}
    p = d["plans"][0]
    assert p["origin"] == "manual" and p["origin_cn"] == "手动" and p["notify"] is True
    assert d["stats"]["by_origin"]["manual"] == 1


def test_origin_rejects_unknown_value(env):
    r = _endpoint("/alert/write")({"action": "add", "type": "PRICE_BELOW", "code": "600271.SH",
                                   "value": 7.0, "origin": "pool"})
    assert r["ok"] is False and "origin" in r["error"]


def test_auto_origin_is_silent_but_visible(env):
    """auto 条目面板照常可查（不删、不藏），但不推送；卡片给出会推送条数。"""
    r = _endpoint("/alert/write")({"action": "add", "type": "PRICE_BELOW",
                                   "code": "600271.SH", "value": 7.0})
    assert r["ok"]
    import alert_store as STORE  # noqa: PLC0415 - 与插件同源
    code, _ = STORE.add_plan({"dp_id": "AL-PRICE_BELOW-600271.SH-6.8",
                              "theme": "航天信息 跌破动能下", "level_tag": "动能下",
                              "condition": {"logic": "AND", "terms": [
                                  {"metric": "stock_price", "code": "600271.SH",
                                   "op": "<", "value": 6.8}]},
                              "confirm_points": 1, "window": "09:35-14:55",
                              "severity": "notify"},
                             source="bootstrap", exit_="L2", origin="auto")
    assert code == "added"
    d = _endpoint("/alert/list", "GET")()
    auto = next(p for p in d["plans"] if p["origin"] == "auto")
    assert auto["origin_cn"] == "自选池" and auto["notify"] is False
    g = next(x for x in d["groups"] if x["code"] == "600271.SH")
    assert g["total"] == 2 and g["push"] == 1


def test_backend_never_imports_order_chain():
    src = open(os.path.join(ROOT, "app", "plugins", "alert", "backend.py"), encoding="utf-8").read()
    names = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    flat = " ".join(names)
    for banned in ("cond_watch", "app.core.paper", "PaperOrderService", "broker"):
        assert banned not in flat, "预警插件不得引入下单链：%s" % banned
