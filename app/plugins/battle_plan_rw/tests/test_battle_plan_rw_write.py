# -*- coding: utf-8 -*-
"""battle_plan_rw 行为测试：7 条写端点的落盘语义 + 幂等细节 + 未知字段保留。

**不碰真生产文件**：真 `PluginContext`（走 core 的 `panel_write` / `state_write`，
备份+原子替换+审计全跑），但四条路径都指到 pytest `tmp_path`：

- `PANEL_V2_WS` / `PANEL_V2_DATA_DIR` / `PANEL_V2_PAPER_DIR` 环境变量指 tmp（与任务书
  §5 口径一致，防止任何模块按环境变量推导生产目录）；
- 另外 monkeypatch `paths.WRITABLE_PANEL` / `READABLE_PANEL` / `WRITABLE_STATE` /
  `paths.OUT_DIR` 的条目到 tmp —— `paths` 的这些字典在 import 期就固化了，只设环境变量
  对已 import 的模块不生效，两层一起做才真正隔离。

语义对照：`engine/server.py` 的 `sign_plan`(7144) / `kill_switch`(7646) / `toggle_item`(7232)
/ `cancel_item`(7180) / `signal_watch_toggle`(7541) / `signal_watch_cancel`(7569) /
`manual_position`(7342)，以及 `_sign_item` / `_sync_plan_sign`(265) / `_apply_engine_state`(298)。
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import paths as core_paths  # noqa: E402
from app.core.contract import PluginContext, validate_manifest  # noqa: E402
from app.plugins.battle_plan_rw import backend  # noqa: E402

PLUGIN_DIR = Path(__file__).resolve().parents[1]

MODULE_KEYS = ("entry", "exit_stop", "exit_profit", "do_t", "notify",
               "sizing", "awake", "vol_ratio_buy", "alert")

PLAN: Dict[str, Any] = {
    "meta": {"trade_date": "2026-10-24", "generated_at": "2026-10-24 01:00:00", "owner": "夏天"},
    "plan_date": "2026-10-24",
    "plan_version": "2026-10-23-v3",
    "sign": {"status": "UNSIGNED", "signed_at": None, "signer": None,
             "note": "待主人盘前签收；未签收=引擎只读模式（安全三锁1）"},
    "position_cap": 100000.0,
    "module_switches": {k: True for k in MODULE_KEYS},
    "notify_types": ["SIGNAL_TRIGGERED"],
    "buy_plan": [
        {"plan_id": "BP-20261024-01", "ts_code": "600000.SH", "name": "浦发银行",
         "status": "PENDING", "enabled": True, "kelly": {"p": 0.6}, "extra_item_key": 1},
        {"plan_id": "BP-20261024-02", "ts_code": "002812.SZ", "name": "恩捷股份",
         "status": "PENDING", "enabled": True},
    ],
    "operations": [
        {"ts_code": "512480.SH", "name": "半导体ETF", "execution_status": "ACTIVE",
         "key_levels": {"stop_loss": 0.792}},
    ],
    "signal_watch": {"orders": [
        {"watch_id": "SW-20261024-01", "ts_code": "300298.SZ", "status": "WATCHING",
         "enabled": True, "trigger": {"below": 10.0}},
    ], "global_rules": {"note": "保持"}},
    "risk_warnings": [{"level": "danger", "text": "x"}],
    "unknown_top_key": {"keep": "me"},
}

DASHBOARD: Dict[str, Any] = {"account": {"total_asset": 200000.0}, "updated_at": "2026-10-24 09:00:00"}

ENGINE_STATE: Dict[str, Any] = {
    "state": "READY", "mode": "READ_ONLY",
    "signed_at": None, "kill_switch_active": False,
    "version": "v0.1.0-dryrun", "trade_mode": "paper",
    "unknown_engine_key": "keep",
}

KILL_SWITCH: Dict[str, Any] = {"active": False, "triggered_at": None, "reason": "解除急停",
                              "history": [{"ts": "2026-10-20 09:00:00", "active": False,
                                           "reason": "解除急停", "snapshot": "x"}]}


# ---------------------------------------------------------------- 沙箱 fixture


class Sandbox:
    """tmp 里的「生产目录」+ 真 PluginContext。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.panel_dir = root / "data" / "panel"
        self.state_dir = root / "engine" / "state"
        self.out_dir = root / "data" / "panel_v2"
        self.panel_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.plan_path = self.panel_dir / "battle_plan.json"
        self.dash_path = self.panel_dir / "dashboard.json"
        self.engine_path = self.state_dir / "engine_state.json"
        self.kill_path = self.state_dir / "kill_switch.json"
        manifest = validate_manifest(
            json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8")), PLUGIN_DIR)
        self.ctx = PluginContext(manifest)

    # -- 落盘/读回 --
    def seed(self, plan: Optional[Dict[str, Any]] = None,
             dashboard: Optional[Dict[str, Any]] = None,
             engine: Optional[Dict[str, Any]] = None,
             kill: Optional[Dict[str, Any]] = None) -> None:
        for path, doc in ((self.plan_path, plan), (self.dash_path, dashboard),
                          (self.engine_path, engine), (self.kill_path, kill)):
            if doc is None:
                continue
            path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    def plan(self) -> Dict[str, Any]:
        return json.loads(self.plan_path.read_text(encoding="utf-8"))

    def engine(self) -> Dict[str, Any]:
        return json.loads(self.engine_path.read_text(encoding="utf-8"))

    def kill(self) -> Dict[str, Any]:
        return json.loads(self.kill_path.read_text(encoding="utf-8"))

    def backups(self, name: str) -> List[Path]:
        return sorted((self.out_dir / "backups" / name).glob("*.json"))

    def audit(self, name: str) -> List[str]:
        path = self.out_dir / "backups" / name / "audit.log"
        if not path.is_file():
            return []
        return path.read_text(encoding="utf-8").strip().splitlines()


@pytest.fixture()
def box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Sandbox:
    # ① 环境变量（任务书口径）：任何按环境变量推导目录的模块都落 tmp
    monkeypatch.setenv("PANEL_V2_WS", str(tmp_path))
    monkeypatch.setenv("PANEL_V2_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PANEL_V2_PAPER_DIR", str(tmp_path / "data" / "panel_v2" / "paper"))

    sandbox = Sandbox(tmp_path)

    # ② 已 import 的 paths 字典/常量也要指到 tmp（import 期已固化）
    monkeypatch.setattr(core_paths, "OUT_DIR", sandbox.out_dir)
    monkeypatch.setitem(core_paths.READABLE_PANEL, "battle_plan", sandbox.plan_path)
    monkeypatch.setitem(core_paths.WRITABLE_PANEL, "battle_plan", sandbox.plan_path)
    monkeypatch.setitem(core_paths.READABLE_PANEL, "dashboard", sandbox.dash_path)
    monkeypatch.setitem(core_paths.WRITABLE_STATE, "engine_state", sandbox.engine_path)
    monkeypatch.setitem(core_paths.WRITABLE_STATE, "kill_switch", sandbox.kill_path)

    sandbox.seed(plan=copy.deepcopy(PLAN), dashboard=copy.deepcopy(DASHBOARD),
                 engine=copy.deepcopy(ENGINE_STATE), kill=copy.deepcopy(KILL_SWITCH))
    return sandbox


def client(box: Sandbox) -> TestClient:
    app = FastAPI()
    app.include_router(backend.register(box.ctx), prefix="/api")
    return TestClient(app)


# ---------------------------------------------------------------- /api/kill


def test_kill_on_snapshots_and_freezes(box: Sandbox) -> None:
    body = client(box).post("/api/kill", json={"active": True, "reason": "主人急停"}).json()
    assert body["ok"] is True
    assert body["module_switches_snapshot"] == {k: True for k in MODULE_KEYS}
    assert body["engine_state"] == "FROZEN"

    doc = box.plan()
    assert all(doc["module_switches"][k] is False for k in MODULE_KEYS), "九键全停"
    st = box.engine()
    assert st["kill_switch_active"] is True and st["state"] == "FROZEN"
    assert st["module_switches_snapshot"] == {k: True for k in MODULE_KEYS}
    ks = box.kill()
    assert ks["active"] is True and ks["triggered_at"]
    assert ks["reason"] == "主人急停"
    assert ks["history"][-1]["active"] is True
    assert ks["history"][-1]["snapshot"] == "快照 8 键原状态"
    assert len(ks["history"]) == len(KILL_SWITCH["history"]) + 1, "history 追加不覆盖"


def test_kill_on_to_on_keeps_existing_snapshot(box: Sandbox) -> None:
    """on→on 重复按：沿用既有快照（防把全 false 快照进去致解除后永久停机）。"""
    client(box).post("/api/kill", json={"active": True, "reason": "第一次"})
    snap_after_first = box.engine()["module_switches_snapshot"]
    assert all(v is True for v in snap_after_first.values())

    body = client(box).post("/api/kill", json={"active": True, "reason": "再按一次"}).json()
    assert body["module_switches_snapshot"] == snap_after_first, "快照不许被全 false 覆盖"
    assert box.kill()["history"][-1]["snapshot"] == "沿用既有快照（防全 false 覆盖）"


def test_kill_off_restores_snapshot_and_clears(box: Sandbox) -> None:
    plan = copy.deepcopy(PLAN)
    plan["module_switches"] = {k: True for k in MODULE_KEYS}
    plan["module_switches"]["do_t"] = False          # 主人本来就关着的模块
    box.seed(plan=plan)

    client(box).post("/api/kill", json={"active": True})
    assert box.plan()["module_switches"]["do_t"] is False

    body = client(box).post("/api/kill", json={"active": False}).json()
    sw = box.plan()["module_switches"]
    assert sw == {k: (k != "do_t") for k in MODULE_KEYS}, "逐键恢复原状态（含原本关闭的模块）"
    assert body["module_switches_snapshot"] is None
    st = box.engine()
    assert st["module_switches_snapshot"] is None
    assert st["kill_switch_active"] is False
    assert st["state"] == "READY", "未签收 → READY"
    assert box.kill()["active"] is False and box.kill()["triggered_at"] is None
    assert box.kill()["reason"] == "解除急停"


def test_kill_fills_missing_module_keys(box: Sandbox) -> None:
    """旧端 `sw.setdefault(k, True)` 补全九键 —— 作战表只写 2 键时也要补全后全停。"""
    plan = copy.deepcopy(PLAN)
    plan["module_switches"] = {"entry": True, "exit_stop": True}
    box.seed(plan=plan)
    client(box).post("/api/kill", json={"active": True})
    sw = box.plan()["module_switches"]
    assert set(sw) == set(MODULE_KEYS)
    assert all(v is False for v in sw.values())


def test_kill_without_battle_plan_still_writes_state(box: Sandbox) -> None:
    """作战表不可读时急停不能被挡住：只写两个状态文件，不拿空文档覆盖作战表。"""
    box.plan_path.unlink()
    body = client(box).post("/api/kill", json={"active": True}).json()
    assert body["ok"] is True
    assert body["engine_state"] == "FROZEN"
    assert body["module_switches_snapshot"] is None
    assert "作战表不可读" in box.kill()["history"][-1]["snapshot"]
    assert not box.plan_path.exists(), "不许凭 404 造出空作战表"


# ---------------------------------------------------------------- /api/toggle


def test_toggle_buy_plan_only_touches_enabled(box: Sandbox) -> None:
    body = client(box).post("/api/toggle",
                            json={"item_type": "buy_plan", "plan_id": "BP-20261024-01",
                                  "enabled": False}).json()
    assert body == {"ok": True, "item_type": "buy_plan", "item": "BP-20261024-01",
                    "enabled": False, "status": "PENDING"}
    item = box.plan()["buy_plan"][0]
    assert item["enabled"] is False
    assert item["status"] == "PENDING", "toggle 不动 status"
    assert "sign" not in item, "toggle 不动 sign"
    assert item["status_history"] == [{"status": "PENDING", "ts": item["status_history"][0]["ts"],
                                      "by": "主人",
                                      "note": "主人停止（enabled=false，不执行/不触发）"}]
    # 引擎状态不受 toggle 影响（旧端 toggle 不调 _apply_engine_state）
    assert box.engine()["state"] == "READY"
    assert not box.backups("engine_state"), "toggle 不写 engine_state"


def test_toggle_operations_by_ts_code(box: Sandbox) -> None:
    body = client(box).post("/api/toggle",
                            json={"item_type": "operations", "ts_code": "512480.SH",
                                  "enabled": True}).json()
    assert body["item"] == "512480.SH" and body["enabled"] is True
    assert body["status"] == "ACTIVE"
    op = box.plan()["operations"][0]
    assert op["status_history"][-1]["note"] == "主人启用"
    assert op["status_history"][-1]["status"] == "ACTIVE"


def test_toggle_validation_and_404_do_not_write(box: Sandbox) -> None:
    before = box.plan_path.read_text(encoding="utf-8")
    c = client(box)
    assert c.post("/api/toggle", json={"item_type": "buy_plan"}).status_code == 422
    assert c.post("/api/toggle", json={"item_type": "operations"}).status_code == 422
    assert c.post("/api/toggle", json={"plan_id": "BP-NOPE"}).status_code == 404
    assert c.post("/api/toggle", json={"item_type": "operations",
                                       "ts_code": "000001.SZ"}).status_code == 404
    assert box.plan_path.read_text(encoding="utf-8") == before


# ---------------------------------------------------------------- /api/cancel


def test_cancel_buy_plan(box: Sandbox) -> None:
    body = client(box).post("/api/cancel",
                            json={"plan_id": "BP-20261024-02", "reason": "改主意"}).json()
    assert body == {"ok": True, "item_type": "buy_plan", "item": "BP-20261024-02",
                    "status": "CANCELLED", "engine_state": "READY"}
    item = box.plan()["buy_plan"][1]
    assert item["status"] == "CANCELLED"
    assert item["status_history"][-1]["status"] == "CANCELLED"
    assert item["status_history"][-1]["note"] == "改主意"


def test_cancel_operations_writes_both_status_fields(box: Sandbox) -> None:
    body = client(box).post("/api/cancel",
                            json={"item_type": "operations", "ts_code": "512480.SH"}).json()
    assert body["status"] == "CANCELLED"
    op = box.plan()["operations"][0]
    assert op["execution_status"] == "CANCELLED" and op["status"] == "CANCELLED"
    assert op["status_history"][-1]["note"] == "主人取消（二次确认）"


def test_cancel_validation_and_404(box: Sandbox) -> None:
    before = box.plan_path.read_text(encoding="utf-8")
    c = client(box)
    assert c.post("/api/cancel", json={"item_type": "buy_plan"}).status_code == 422
    assert c.post("/api/cancel", json={"item_type": "operations"}).status_code == 422
    assert c.post("/api/cancel", json={"plan_id": "BP-NOPE"}).status_code == 404
    assert c.post("/api/cancel", json={"item_type": "operations",
                                       "ts_code": "000001.SZ"}).status_code == 404
    assert box.plan_path.read_text(encoding="utf-8") == before


# ---------------------------------------------------------------- /api/signal_watch/*


def test_signal_watch_toggle_does_not_touch_status_or_history(box: Sandbox) -> None:
    body = client(box).post("/api/signal_watch/toggle",
                            json={"watch_id": "SW-20261024-01", "enabled": False}).json()
    assert body == {"ok": True, "watch_id": "SW-20261024-01",
                    "enabled": False, "status": "WATCHING"}
    order = box.plan()["signal_watch"]["orders"][0]
    assert order["enabled"] is False
    assert order["status"] == "WATCHING", "择机单 toggle 不动 status"
    assert "status_history" not in order, "S3 契约里没有 status_history"
    assert box.plan()["signal_watch"]["global_rules"] == {"note": "保持"}


def test_signal_watch_toggle_without_enabled_does_not_write(box: Sandbox) -> None:
    before = box.plan_path.read_text(encoding="utf-8")
    body = client(box).post("/api/signal_watch/toggle",
                            json={"watch_id": "SW-20261024-01"}).json()
    assert body["enabled"] is True, "缺省语义：未设置视为启用"
    assert box.plan_path.read_text(encoding="utf-8") == before, "未传 enabled 不写盘"
    assert not box.backups("battle_plan"), "未传 enabled 不产生备份（即没写）"


def test_signal_watch_cancel(box: Sandbox) -> None:
    body = client(box).post("/api/signal_watch/cancel",
                            json={"watch_id": "SW-20261024-01", "reason": "不追了"}).json()
    assert body == {"ok": True, "watch_id": "SW-20261024-01", "status": "CANCELLED"}
    order = box.plan()["signal_watch"]["orders"][0]
    assert order["status"] == "CANCELLED"
    assert "status_history" not in order
    assert order["enabled"] is True, "cancel 只改 status"


def test_signal_watch_unknown_id_404(box: Sandbox) -> None:
    before = box.plan_path.read_text(encoding="utf-8")
    c = client(box)
    assert c.post("/api/signal_watch/toggle",
                  json={"watch_id": "SW-NOPE", "enabled": True}).status_code == 404
    assert c.post("/api/signal_watch/cancel", json={"watch_id": "SW-NOPE"}).status_code == 404
    assert c.post("/api/signal_watch/toggle", json={}).status_code == 422
    assert box.plan_path.read_text(encoding="utf-8") == before


def test_signal_watch_missing_section_is_created(box: Sandbox) -> None:
    """缺 signal_watch 段时 toggle 不崩：404（orders 补空列表，写回只在成功路径）。"""
    plan = copy.deepcopy(PLAN)
    plan.pop("signal_watch")
    box.seed(plan=plan)
    assert client(box).post("/api/signal_watch/toggle",
                            json={"watch_id": "SW-1", "enabled": False}).status_code == 404


# ---------------------------------------------------------------- /api/manual_position


def test_manual_position_uses_position_cap(box: Sandbox) -> None:
    body = client(box).post("/api/manual_position", json={"pct": 25}).json()
    assert body["ok"] is True
    assert body["manual_position"]["pct"] == 25
    assert body["manual_position"]["amount"] == 25000.0, "25% × position_cap 100000"
    assert body["manual_position"]["updated_at"]
    doc = box.plan()
    assert doc["manual_position"] == body["manual_position"]
    # kelly 联动：单票上限 = 总仓位 × 50%
    for item in doc["buy_plan"]:
        assert item["kelly"]["size_amount"] == 12500.0
    assert doc["buy_plan"][0]["kelly"]["p"] == 0.6, "kelly 其他字段保留"


def test_manual_position_falls_back_to_total_asset(box: Sandbox) -> None:
    plan = copy.deepcopy(PLAN)
    plan.pop("position_cap")
    box.seed(plan=plan)
    body = client(box).post("/api/manual_position", json={"pct": 10}).json()
    assert body["manual_position"]["amount"] == 20000.0, "10% × dashboard total_asset 200000"


def test_manual_position_zero_amount_when_no_basis(box: Sandbox) -> None:
    plan = copy.deepcopy(PLAN)
    plan.pop("position_cap")
    box.seed(plan=plan)
    box.dash_path.unlink()
    body = client(box).post("/api/manual_position", json={"pct": 10}).json()
    assert body["manual_position"]["amount"] == 0.0, "拿不到基准就落 0，不猜"
    assert all("kelly" not in it or "size_amount" not in it["kelly"]
               for it in box.plan()["buy_plan"]), "amount=0 不联动 kelly"


def test_manual_position_zero_pct_is_legal(box: Sandbox) -> None:
    """0 = 合法值（AMV 极端档禁开新仓）→ amount 落 0，不回退总资产。"""
    body = client(box).post("/api/manual_position", json={"pct": 0}).json()
    assert body["manual_position"] == {"pct": 0, "amount": 0.0,
                                       "updated_at": body["manual_position"]["updated_at"]}


@pytest.mark.parametrize("bad", [{"pct": 101}, {"pct": -1}, {"pct": "abc"}, {}])
def test_manual_position_rejects_bad_pct(box: Sandbox, bad: Dict[str, Any]) -> None:
    before = box.plan_path.read_text(encoding="utf-8")
    assert client(box).post("/api/manual_position", json=bad).status_code == 422
    assert box.plan_path.read_text(encoding="utf-8") == before


# ---------------------------------------------------------------- 通用：未知字段 / 备份审计


def test_unknown_fields_preserved_across_all_writes(box: Sandbox) -> None:
    c = client(box)
    c.post("/api/toggle", json={"plan_id": "BP-20261024-01", "enabled": False})
    c.post("/api/signal_watch/toggle", json={"watch_id": "SW-20261024-01", "enabled": False})
    c.post("/api/manual_position", json={"pct": 5})
    c.post("/api/cancel", json={"plan_id": "BP-20261024-02"})

    doc = box.plan()
    assert set(doc) == set(PLAN) | {"manual_position"}, "顶层键集合只允许新增 manual_position"
    assert doc["unknown_top_key"] == {"keep": "me"}
    assert doc["meta"]["owner"] == "夏天", "meta 未知键保留"
    assert doc["buy_plan"][0]["extra_item_key"] == 1, "条目未知键保留"
    assert doc["signal_watch"]["orders"][0]["trigger"] == {"below": 10.0}
    assert doc["notify_types"] == ["SIGNAL_TRIGGERED"]
    assert doc["risk_warnings"] == PLAN["risk_warnings"]


def test_writes_go_through_backup_and_audit(box: Sandbox) -> None:
    client(box).post("/api/kill", json={"active": True})
    plan_backups = box.backups("battle_plan")
    assert plan_backups, "battle_plan 写前必须备份"
    assert json.loads(plan_backups[-1].read_text(encoding="utf-8"))["sign"]["status"] == "UNSIGNED"
    assert any("plugin=battle_plan_rw" in line and "file=battle_plan" in line
               for line in box.audit("battle_plan"))

    assert box.backups("engine_state"), "engine_state 写前必须备份"
    assert any("file=engine_state" in line for line in box.audit("engine_state"))
    assert not box.plan_path.with_name("battle_plan.json.tmp").exists(), "不留 .tmp"


def test_kill_backs_up_kill_switch(box: Sandbox) -> None:
    client(box).post("/api/kill", json={"active": True})
    assert box.backups("kill_switch")
    assert any("file=kill_switch" in line for line in box.audit("kill_switch"))


def test_no_write_when_plan_unreadable(box: Sandbox, monkeypatch: pytest.MonkeyPatch) -> None:
    """读失败就不写（防拿空文档覆盖生产文件）。"""
    box.plan_path.write_text("{not json", encoding="utf-8")
    before = box.plan_path.read_text(encoding="utf-8")
    resp = client(box).post("/api/toggle", json={"plan_id": "BP-20261024-01", "enabled": False})
    assert resp.status_code == 503
    assert "拒绝写入" in resp.json()["detail"]
    assert box.plan_path.read_text(encoding="utf-8") == before
    assert not box.backups("battle_plan")


# ---------------------------------------------------------------- state_json 通道（N2 落地后）

class _CtxWithStateJson:
    """假 ctx：验证 `state_json` 存在时优先走 ctx 通道（不碰真文件）。"""

    def __init__(self, plan: Dict[str, Any], engine: Dict[str, Any], kill: Dict[str, Any]) -> None:
        self.id = "battle_plan_rw"
        self.logger = None
        self._docs = {"battle_plan": copy.deepcopy(plan), "dashboard": copy.deepcopy(DASHBOARD)}
        self._state = {"engine_state": copy.deepcopy(engine), "kill_switch": copy.deepcopy(kill)}
        self.writes: List[str] = []

    def panel_json(self, name: str) -> Dict[str, Any]:
        if name not in self._docs:
            raise FileNotFoundError(name)
        return copy.deepcopy(self._docs[name])

    def panel_write(self, name: str, payload: Dict[str, Any]) -> None:
        self.writes.append("panel:" + name)
        self._docs[name] = copy.deepcopy(payload)

    def state_json(self, name: str) -> Dict[str, Any]:
        return copy.deepcopy(self._state[name])

    def state_write(self, name: str, payload: Dict[str, Any]) -> None:
        self.writes.append("state:" + name)
        self._state[name] = copy.deepcopy(payload)


def test_state_json_channel_preferred() -> None:
    ctx = _CtxWithStateJson(PLAN, ENGINE_STATE, KILL_SWITCH)
    app = FastAPI()
    app.include_router(backend.register(ctx), prefix="/api")
    # 2026-11-07：签收链删除后，这两个用例改用仍在的写通道 /api/kill 验证同一套 state_json 优先链
    body = TestClient(app).post("/api/kill", json={"active": True}).json()
    assert body["engine_state"] == "FROZEN"
    assert ctx.writes == ["panel:battle_plan", "state:engine_state", "state:kill_switch"]
    assert ctx._state["engine_state"]["unknown_engine_key"] == "keep"


def test_state_json_channel_used_by_kill() -> None:
    ctx = _CtxWithStateJson(PLAN, ENGINE_STATE, KILL_SWITCH)
    app = FastAPI()
    app.include_router(backend.register(ctx), prefix="/api")
    TestClient(app).post("/api/kill", json={"active": True})
    assert ctx.writes == ["panel:battle_plan", "state:engine_state", "state:kill_switch"]
    assert ctx._state["kill_switch"]["active"] is True


class _NoStateJsonCtx:
    """真去楜 `state_json` 属性（getattr 拿不到 → 走白名单只读兼底）。"""

    def __init__(self, manifest: Any, writes: List[str]) -> None:
        self.manifest = manifest
        self.id = "battle_plan_rw"
        self.logger = None
        self._writes = writes

    def panel_json(self, name: str) -> Dict[str, Any]:
        return copy.deepcopy(PLAN)

    def panel_write(self, name: str, payload: Dict[str, Any]) -> None:
        self._writes.append("panel:" + name)

    def state_write(self, name: str, payload: Dict[str, Any]) -> None:
        self._writes.append("state:" + name)


def test_state_read_falls_back_to_whitelist_path(box: Sandbox) -> None:
    """老 core（无 `ctx.state_json`）时读走 `paths.WRITABLE_STATE` 白名单只读兼底，
    写仍只走 `ctx.state_write`；未知键一样保留。"""
    writes: List[str] = []
    ctx = _NoStateJsonCtx(box.ctx.manifest, writes)
    app = FastAPI()
    app.include_router(backend.register(ctx), prefix="/api")
    body = TestClient(app).post("/api/kill", json={"active": True}).json()
    assert body["engine_state"] == "FROZEN"
    assert writes == ["panel:battle_plan", "state:engine_state", "state:kill_switch"]
    # 假 ctx 不真落盘，所以文件保持原样；行内的 `engine_state` 来自兼底读取 + 内存同步
    assert box.engine()["unknown_engine_key"] == "keep"
    assert box.engine()["state"] == "READY", "假 ctx 的 state_write 不写文件（只记调用）"
