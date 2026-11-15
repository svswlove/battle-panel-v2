
# ---------------------------------------------------------------- /api/module_switches_batch（M1 C1）
#
# 语义唯一源：旧 engine/server.py:7400 module_switches_batch；前端 index.html:9071 confirmBatch()。
# 锁住的四件事：非法键 422 / 只更新传入键 / 空提交不写盘 / 一次写盘含 manual_position+notify_types。
#
# 夹具（box / client / Sandbox / PLAN / MODULE_KEYS）与行为测试同一套：
# 从兄弟模块导入，不另建一套（否则两份 PLAN 一漂就双源）。
import ast
import copy
import re
import sys as _sys
from pathlib import Path as _Path

import pytest

_HERE = _Path(__file__).resolve().parent
if str(_HERE) not in _sys.path:
    _sys.path.insert(0, str(_HERE))

from test_battle_plan_rw_write import (  # noqa: E402
    MODULE_KEYS,
    PLAN,
    Sandbox,
    box,      # noqa: F401 - pytest fixture 转发（同名导入即注册）
    client,
)

ROOT = _HERE.parents[3]
if str(ROOT) not in _sys.path:
    _sys.path.insert(0, str(ROOT))

from app.plugins.battle_plan_rw import backend  # noqa: E402



def test_batch_switches_only_updates_passed_keys(box: Sandbox) -> None:
    body = client(box).post("/api/module_switches_batch",
                            json={"switches": {"entry": False}, "signer": "主人"}).json()
    assert body["ok"] is True
    sw = body["module_switches"]
    assert sw["entry"] is False
    # 未传的键保持原值（PLAN 里全 true）
    assert sw["exit_stop"] is True and sw["alert"] is True
    assert set(sw) == set(MODULE_KEYS), "落盘补全九键"
    doc = box.plan()
    assert doc["module_switches"] == sw
    assert doc["module_switches"]["entry"] is False
    assert doc["unknown_top_key"] == {"keep": "me"}, "未知顶层键原样保留"
    assert PLAN["module_switches"]["entry"] is True
    assert box.backups("battle_plan"), "写前必须备份"


def test_batch_switches_invalid_key_422_and_no_write(box: Sandbox) -> None:
    before = box.plan_path.read_text(encoding="utf-8")
    res = client(box).post("/api/module_switches_batch", json={"switches": {"nope": True}})
    assert res.status_code == 422
    assert "非法模块键" in res.json()["detail"] and "nope" in res.json()["detail"]
    assert box.plan_path.read_text(encoding="utf-8") == before, "校验不过不许写盘"
    assert box.backups("battle_plan") == []


def test_batch_switches_empty_submit_is_noop(box: Sandbox) -> None:
    """空提交（无 switches / manual_position / notify_types）：不写盘，直接回现状。"""
    before = box.plan_path.read_text(encoding="utf-8")
    body = client(box).post("/api/module_switches_batch", json={}).json()
    assert body["ok"] is True
    assert body["module_switches"] == PLAN["module_switches"]
    assert body["notify_types"] == PLAN["notify_types"]
    assert box.plan_path.read_text(encoding="utf-8") == before
    assert box.backups("battle_plan") == [], "空提交不得产生备份（内容不动）"


def test_batch_manual_position_uses_position_cap_and_syncs_kelly(box: Sandbox) -> None:
    body = client(box).post("/api/module_switches_batch",
                            json={"manual_position": {"pct": 30}}).json()
    mp = body["manual_position"]
    assert mp["pct"] == 30.0
    assert mp["amount"] == 30000.0, "pct% × position_cap(100000)"
    assert mp["updated_at"]
    doc = box.plan()
    assert doc["manual_position"] == mp
    for it in doc["buy_plan"]:
        assert it["kelly"]["size_amount"] == 15000.0, "单票上限 = 总仓位 × 50%"
    assert doc["buy_plan"][0]["kelly"]["p"] == 0.6, "kelly 其他字段保留"


def test_batch_manual_position_falls_back_to_total_asset(box: Sandbox) -> None:
    doc = copy.deepcopy(PLAN)
    doc.pop("position_cap", None)
    box.seed(plan=doc)
    body = client(box).post("/api/module_switches_batch",
                            json={"manual_position": {"pct": 10}}).json()
    assert body["manual_position"]["amount"] == 20000.0, \
        "position_cap 缺失 → 回退 dashboard.total_asset"


def test_batch_manual_position_zero_is_legal(box: Sandbox) -> None:
    """0 = 合法值（AMV 极端档禁开新仓）→ amount 落 0，不回退总资产。"""
    doc = copy.deepcopy(PLAN)
    doc["position_cap"] = 0
    box.seed(plan=doc)
    body = client(box).post("/api/module_switches_batch",
                            json={"manual_position": {"pct": 0}}).json()
    assert body["manual_position"]["amount"] == 0.0


@pytest.mark.parametrize("bad", [{"pct": -1}, {"pct": 101}, {"pct": "x"}, {"pct": True}, {}])
def test_batch_manual_position_invalid_422(box: Sandbox, bad) -> None:
    before = box.plan_path.read_text(encoding="utf-8")
    res = client(box).post("/api/module_switches_batch", json={"manual_position": bad})
    assert res.status_code == 422, bad
    assert "manual_position.pct" in res.json()["detail"]
    assert box.plan_path.read_text(encoding="utf-8") == before


def test_batch_notify_types_dedup_keeps_order(box: Sandbox) -> None:
    body = client(box).post("/api/module_switches_batch", json={
        "notify_types": ["STOP_TRIGGERED", "ORDER_FILLED", "STOP_TRIGGERED"],
    }).json()
    assert body["notify_types"] == ["STOP_TRIGGERED", "ORDER_FILLED"], "去重保序"
    assert box.plan()["notify_types"] == ["STOP_TRIGGERED", "ORDER_FILLED"]


def test_batch_notify_types_unknown_422(box: Sandbox) -> None:
    res = client(box).post("/api/module_switches_batch",
                           json={"notify_types": ["ORDER_FILLED", "NOPE"]})
    assert res.status_code == 422
    assert "非法推送类型" in res.json()["detail"] and "NOPE" in res.json()["detail"]


def test_batch_all_three_in_one_write(box: Sandbox) -> None:
    """三样同时提交 → 一次写盘（只有一个备份），响应键与旧端逐键一致。"""
    body = client(box).post("/api/module_switches_batch", json={
        "switches": {"do_t": False, "notify": False},
        "manual_position": {"pct": 50},
        "notify_types": ["SIGNAL_TRIGGERED"],
        "signer": "主人",
    }).json()
    assert set(body) == {"ok", "module_switches", "manual_position", "notify_types"}
    assert len(box.backups("battle_plan")) == 1, "一次提交只写一次盘"
    doc = box.plan()
    assert doc["module_switches"]["do_t"] is False and doc["module_switches"]["notify"] is False
    assert doc["manual_position"]["amount"] == 50000.0
    assert doc["notify_types"] == ["SIGNAL_TRIGGERED"]


def test_batch_missing_plan_404(box: Sandbox) -> None:
    box.plan_path.unlink()
    res = client(box).post("/api/module_switches_batch", json={"switches": {"entry": True}})
    assert res.status_code == 404 and "battle_plan 无数据" in res.json()["detail"]


def test_notify_type_keys_match_legacy() -> None:
    """内置的 9 类推送枚举必须与旧仓 `common/push_queue.py::NOTIFY_TYPE_KEYS` 逐项一致。

    这是防「双源漂移」的锁：旧仓改枚举时本测试直接红，不会静默漂成
    「面板能勾、引擎不认」或反之。
    """
    import ast
    import re

    from app.core import paths as core_paths

    src = (core_paths.WS / "engine" / "common" / "push_queue.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    legacy: Optional[tuple] = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "NOTIFY_TYPE_KEYS" for t in node.targets):
            legacy = tuple(ast.literal_eval(node.value))
            break
    assert legacy is not None, "旧仓 push_queue 里找不到 NOTIFY_TYPE_KEYS"
    assert backend.NOTIFY_TYPE_KEYS == legacy
    # MODULE_KEYS 同理（九键）。2026-10-30：旧仓 `engine/server.py` 退役删除，
    # 原本从该文件解析做对拍，现改为冻结基线（值取自退役前最后一版，逐项一致）——
    # 作用是锁键集：增删键会红，不会静默漂成「面板能勾、引擎不认」。
    frozen_module_keys = ("entry", "exit_stop", "exit_profit", "do_t", "notify",
                          "sizing", "awake", "vol_ratio_buy", "alert")
    assert backend.MODULE_KEYS == frozen_module_keys
