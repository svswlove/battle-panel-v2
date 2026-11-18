# -*- coding: utf-8 -*-
"""compat 插件契约测试：只读、旧格式逐字段、无写接口。

不碰生产数据：FakeCtx 全内存；行情一律注入 quotes_fn（零真实 IO）。
挂载前缀按 manifest 的 `backend.mount="root"` → `/api`（旧前端直连根路径）。
"""
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.contract import (  # noqa: E402
    find_forbidden_db_usage,
    find_forbidden_quote_usage,
    validate_manifest,
)
from app.plugins.compat import backend  # noqa: E402

PLUGIN_DIR = Path(__file__).resolve().parents[1]
PREFIX = "/api"          # mount=root


@pytest.fixture(autouse=True)
def _force_intraday(monkeypatch):
    """2026-11-07：盘后落盘缓存按本地时钟切 live。

    单测默认固定为「盘中」，否则周末/夜间跑会因 `_rs_is_postclose()` 把 live 归零而集体变红；
    盘后行为由单独用例显式打桩验证。
    """
    monkeypatch.setattr(backend, "_rs_is_postclose", lambda *a, **k: False, raising=False)


# ---------------------------------------------------------------- 固件
def _manifest_raw():
    with open(PLUGIN_DIR / "plugin.json", encoding="utf-8") as f:
        return json.load(f)


DASHBOARD = {
    "meta": {"trade_date": "2026-10-23"},
    "engine": {"state": "ARMED", "mode": "ARMED",
               "kill_switch": {"active": False, "triggered_at": None, "reason": "解除急停"},
               "battle_plan_signed": True},
    "account": {"total_asset": 217389.4, "market_value": 217083.6},
    "positions": [],
    "orders_today": [],
    "environment": {"market_regime": "RANGE"},
    "alerts": [],
    "risk_locks": {"battle_plan_signed": True},
}

BATTLE_PLAN = {
    "meta": {"trade_date": "2026-10-23"},
    "sign": {"status": "SIGNED"},
    "operations": [
        {"ts_code": "512480.SH", "name": "半导体ETF", "plan": "P1", "action_type": "HOLD",
         "key_levels": {"stop_loss": 1.00}, "enabled": True},
        {"ts_code": "600000.SH", "name": "浦发银行", "plan": "P2", "action_type": "BUY",
         "key_levels": {}, "enabled": True},
    ],
}

POSITIONS = {"meta": {"generated_at": "2026-09-28 10:05:11"}, "date": "2026-08-24",
             "updated_at": "2026-09-28 10:05:11", "holdings": [], "selection": {},
             "track_signals": [], "source_audit": {}}

REAL_POSITIONS = {
    "meta": {"trade_date": "2026-10-23"}, "status": "CONNECTED",
    "updated_at": "2026-10-23 17:44:08",
    "positions": [{"code": "512480.SH", "name": "半导体ETF国联安", "qty": 100,
                   "available_qty": 100, "cost_price": 1.061, "market_value": 83.6}],
    "account": {"total_asset": 217389.4},
}

INTRADAY = {"meta": {"generated_at": "2099-01-01 00:00:00", "source": "qmt:tick-feed"},
            "market_breadth": {"limit_up": 82, "limit_down": 9,
                               "source": "qmt:full-market-snapshot", "asof": "2099-01-01 00:00:00"},
            "signals": []}

WATCHLIST_QUOTES = {"asof": "2099-01-01 00:00:00",
                    "meta": {"source": "tick_feed:full-tick"},
                    "quotes": {"512480.SH": {"price": 0.836, "pre_close": 0.83, "change_pct": 0.72}}}

ALERT_TODO = {"updated_at": "2026-10-23 15:03:57",
              "alerts": [{"level": "danger", "message": "x", "ts": "t", "source": "defense",
                          "ts_code": None}],
              "todos": []}

AMV_LIVE = {"date": "2026-10-23", "change": -0.001}
REGIME = {"date": "2026-10-23", "state": "空"}
POSITION_CAP = {"trade_date": "2026-10-23", "final_pct": 30}
ALERT_PLAN = {"meta": {"persistent": True}, "plans": []}

# engine/state 两份状态文件（0.3.0：`/api/status` 从这里读 trade_mode / kill_switch）
ENGINE_STATE = {"state": "ARMED", "mode": "ARMED", "battle_plan_signed": True,
                "kill_switch_active": False, "trade_mode": "paper",
                "version": "v0.1.0-dryrun", "updated_at": "2026-10-24 18:38:48"}
KILL_SWITCH = {"active": False, "triggered_at": None, "reason": "解除急停",
               "updated_at": "2026-10-24 18:38:48"}

STOCK_NAMES = {"names": {"512480": "半导体ETF国联安", "600000": "浦发银行",
                         "920992": "中科美菱", "880005": "涨跌家数", "300750": "宁德时代"}}


class FakeCtx:
    """最小 ctx：只实现契约允许的只读接口。"""

    def __init__(self, panels=None, json_files=None, state_files=None):
        self.panels = dict(panels or {})
        self.json_files = dict(json_files or {})
        self.state_files = dict(state_files or {})
        self.db_calls = []
        self.quotes_used = False
        self.state_calls = []
        self.cache_docs = {}

    def cache_read(self, name):
        """2026-11-07：插件落盘缓存（内存替身；core 真实现写到 data/panel_v2/_cache）。"""
        if name not in self.cache_docs:
            raise FileNotFoundError(name)
        return json.loads(json.dumps(self.cache_docs[name]))

    def cache_write(self, name, payload):
        self.cache_docs[name] = json.loads(json.dumps(payload))

    def panel_json(self, name):
        if name not in self.panels:
            raise FileNotFoundError(name)
        return self.panels[name]

    def json_read(self, name):
        if name not in self.json_files:
            raise FileNotFoundError(name)
        return self.json_files[name]

    def state_json(self, name):
        """core 的引擎状态只读通道（N2 落地）：沙箱里没登记 → FileNotFoundError。"""
        self.state_calls.append(name)
        if name not in self.state_files:
            raise FileNotFoundError(name)
        return self.state_files[name]

    def db_read(self, name):
        """测试沙箱无库 → 抛 FileNotFoundError，调用方必须如实降级（不编造）。"""
        self.db_calls.append(name)
        raise FileNotFoundError("%s 不在测试沙箱" % name)

    @property
    def quotes(self):
        """行情门面：测试里恒不可用 → 调用方降级为「只有 watchlist_quotes」/空。"""
        self.quotes_used = True
        return None

    @property
    def paper(self):
        """纸面账本：测试里未注入 → PermissionError，持仓门回落实盘分支（旧语义）。

        要测 paper 分支的用例直接给 `ctx.paper_override` 赋值（见 `_full_ctx`）。
        """
        override = getattr(self, "paper_override", None)
        if override is not None:
            return override
        raise PermissionError("测试未提供纸面账本")


def _full_ctx(**overrides):
    panels = {
        "dashboard": DASHBOARD, "battle_plan": BATTLE_PLAN, "positions": POSITIONS,
        "real_positions": REAL_POSITIONS, "intraday": INTRADAY,
        "watchlist_quotes": WATCHLIST_QUOTES, "alert_todo": ALERT_TODO,
        "amv_live": AMV_LIVE, "amv_regime_latest": REGIME,
        "position_cap_latest": POSITION_CAP, "alert_plan_persistent": ALERT_PLAN,
    }
    panels.update(overrides.pop("panels", {}))
    for key in overrides.pop("drop", ()):
        panels.pop(key, None)
    states = {"engine_state": ENGINE_STATE, "kill_switch": KILL_SWITCH}
    states.update(overrides.pop("states", {}))
    for key in overrides.pop("drop_states", ()):
        states.pop(key, None)
    return FakeCtx(panels=panels, json_files={"stock_names": STOCK_NAMES}, state_files=states)


def _client(ctx):
    """按 manifest 的 `backend.mount="root"` 挂载（core 统一前缀 `/api`）。

    为什么不是 `/api/p/compat`：旧前端直接请求 `/api/dashboard.json` 这类根路径，
    迁移目标是「旧前端只改 ENGINE 常量、其余一行不动」，所以必须挂到 `/api`。
    """
    app = FastAPI()
    app.include_router(backend.register(ctx), prefix=PREFIX)
    return TestClient(app)


def _no_quotes(_codes):
    """零行情：所有票据缺价 → 各端点应降级为 null/UNKNOWN，不编造。"""
    return {}


# ---------------------------------------------------------------- manifest / 静态扫描
def test_manifest_valid_and_readonly():
    m = validate_manifest(_manifest_raw(), PLUGIN_DIR)
    assert m.id == "compat"
    assert m.data_write == ()
    assert m.backend.mount == "root", "旧前端直连 /api/... 根路径，必须挂 root"
    # 任务书 §2.1 的 9 个端点数据源都要在 data.panel 里声明
    for name in ("battle_plan", "amv_live", "amv_regime_latest", "position_cap_latest",
                 "alert_todo", "positions", "real_positions", "dashboard"):
        assert name in m.panel_read, name
    assert m.frontend.cards == ()


def test_backend_has_no_forbidden_io():
    source = (PLUGIN_DIR / "backend.py").read_text(encoding="utf-8")
    assert find_forbidden_db_usage(source) == []
    assert find_forbidden_quote_usage(source) == []


@pytest.mark.parametrize("token", ["open(\"w", "open('w", ".write_text(", "json.dump(",
                                   "os.replace(", "requests.post", "subprocess.", ".mkdir(",
                                   "INSERT INTO", "UPDATE ", "DELETE FROM", ".to_sql("])
def test_backend_clean_of_write_tokens(token):
    """cutover_gate 的「插件无写代码」静态扫描口径（提前在单测里卡住）。"""
    assert token not in (PLUGIN_DIR / "backend.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------- §2.1 静态转发
@pytest.mark.parametrize("path,panel", [
    ("/api/battle_plan.json", "battle_plan"),
    ("/api/amv_live.json", "amv_live"),
    ("/api/amv_regime_latest.json", "amv_regime_latest"),
    ("/api/position_cap_latest.json", "position_cap_latest"),
    ("/api/alert_todo.json", "alert_todo"),
    ("/api/positions.json", "positions"),
    ("/api/dashboard.json", "dashboard"),
])
def test_static_passthrough_is_byte_identical(path, panel):
    """透传类端点必须**逐字段原样**（不重排、不补字段、不删字段）。"""
    ctx = _full_ctx()
    assert _client(ctx).get(path).json() == ctx.panels[panel]


@pytest.mark.parametrize("name", ["battle_plan", "amv_live", "amv_regime_latest",
                                  "position_cap_latest", "alert_todo", "positions", "dashboard"])
def test_static_missing_file_returns_404_with_detail(name):
    ctx = _full_ctx(drop=(name,))
    res = _client(ctx).get("/api/%s.json" % name)
    assert res.status_code == 404
    assert "detail" in res.json()


def test_alert_plan_missing_returns_empty_skeleton_not_404():
    """旧端口径：长期预警缺失 → 200 + 空 plans（§4.6），不是 404。"""
    ctx = _full_ctx(drop=("alert_plan_persistent",))
    res = _client(ctx).get("/api/alert_plan.json")
    assert res.status_code == 200
    body = res.json()
    assert body["plans"] == []
    assert body["meta"] == {"persistent": True, "generated_at": "", "source": "server"}


def test_push_types_shape():
    body = _client(_full_ctx()).get("/api/push_types.json").json()
    assert set(body) == {"types", "labels", "all_whitelist", "channel"}
    assert len(body["types"]) == 9
    assert body["labels"]["ORDER_FILLED"] == "买入成交"
    assert body["all_whitelist"] == sorted(body["all_whitelist"])
    assert set(body["channel"]) == {"mode", "transport", "queue_file", "note", "levels"}


def test_concept_moved_to_dedicated_plugin():
    """`/api/concept.json` 已移交独立 `concept` 插件（2026-10-23，任务书 4.2）。

    本插件**不得**再注册该路由：两个 root 插件抢同一路径时 core 启动即报冲突
    （`app/core/main.py` 的 claimed 保护）。这里锁住「compat 侧没有这条路由」。
    """
    router = backend.register(_full_ctx())
    assert "/concept.json" not in {r.path for r in router.routes}


# ---------------------------------------------------------------- /status
def test_status_shape_and_values():
    """0.3.0：`trade_mode` 从 `engine_state.json` 经 `ctx.state_json` 读出（不再恒 null）。"""
    ctx = _full_ctx()
    body = _client(ctx).get("/api/status").json()
    assert set(body) == {"code", "label", "state", "mode",
                         "kill_switch_active", "kill_reason", "trade_mode", "updated_at"}
    assert body["code"] == "RUNNING" and body["label"] == "运行中"
    assert body["state"] == "ARMED" and body["mode"] == "ARMED"
    assert body["kill_switch_active"] is False
    assert body["trade_mode"] == "paper", "engine_state.trade_mode 原样带出（8800 同口径）"
    assert ctx.state_calls == ["engine_state", "kill_switch"], "只读这两个状态文件"


def test_status_trade_mode_live_and_dirty_value_passthrough():
    """8800 是 `st.get("trade_mode", "live")`：脏值原样带出，**不做合法性校验**。"""
    body = _client(_full_ctx(states={"engine_state": {"trade_mode": "live"}})).get(
        "/api/status").json()
    assert body["trade_mode"] == "live"
    body = _client(_full_ctx(states={"engine_state": {"trade_mode": "xxx"}})).get(
        "/api/status").json()
    assert body["trade_mode"] == "xxx"


def test_status_trade_mode_defaults_live_without_engine_state():
    """engine_state 缺失 → `live`（旧端 `_load_engine_state` 缺省同口径），不是 null。"""
    body = _client(_full_ctx(drop_states=("engine_state",))).get("/api/status").json()
    assert body["trade_mode"] == "live"


def test_status_without_state_channel_still_200():
    """老 core（无 `ctx.state_json`）→ 不报错，trade_mode 退 live、急停退 dashboard 快照。"""
    class _NoStateCtx(FakeCtx):
        state_json = None  # 属性存在但不可调用 → 走 None 分支

    ctx = _NoStateCtx(panels=dict(_full_ctx().panels),
                      json_files={"stock_names": STOCK_NAMES})
    body = _client(ctx).get("/api/status").json()
    assert body["trade_mode"] == "live"
    assert body["code"] == "RUNNING"


def test_status_ignores_battle_plan_sign_after_removal():
    """2026-11-07：签收链删除后，状态不再看 battle_plan.sign（缺表也不影响图标）。"""
    ctx = _full_ctx(drop=("battle_plan",))
    body = _client(ctx).get("/api/status").json()
    assert body["code"] == "RUNNING" and body["state"] == "ARMED"
    assert "battle_plan_signed" not in body


def test_status_frozen_when_kill_switch_active():
    """急停态以 `kill_switch.json` 为准（8800 `_load_kill_switch()` 读的同一份文件）。"""
    dash = json.loads(json.dumps(DASHBOARD))
    dash["engine"]["kill_switch"] = {"active": True, "reason": "主人急停"}
    kill = dict(KILL_SWITCH, active=True, reason="主人急停")
    ctx = _full_ctx(panels={"dashboard": dash}, states={"kill_switch": kill})
    body = _client(ctx).get("/api/status").json()
    assert body["code"] == "FROZEN" and body["label"] == "已急停"
    assert body["state"] == "FROZEN"
    assert body["kill_reason"] == "主人急停"


def test_status_kill_falls_back_to_dashboard_snapshot():
    """`kill_switch.json` 读不到 → 退 `dashboard.engine.kill_switch`（同源快照）。"""
    dash = json.loads(json.dumps(DASHBOARD))
    dash["engine"]["kill_switch"] = {"active": True, "reason": "快照急停"}
    ctx = _full_ctx(panels={"dashboard": dash}, drop_states=("kill_switch",))
    body = _client(ctx).get("/api/status").json()
    assert body["code"] == "FROZEN" and body["kill_reason"] == "快照急停"


def test_status_kill_switch_file_wins_over_dashboard_snapshot():
    """两个源不一致时以 `kill_switch.json` 为准（旧端 `_load_kill_switch()` 唯一源）。"""
    dash = json.loads(json.dumps(DASHBOARD))
    dash["engine"]["kill_switch"] = {"active": True, "reason": "旧快照"}
    kill = dict(KILL_SWITCH, active=False, reason="解除急停")
    body = _client(_full_ctx(panels={"dashboard": dash},
                             states={"kill_switch": kill})).get("/api/status").json()
    assert body["kill_switch_active"] is False
    assert body["kill_reason"] == "解除急停"
    assert body["code"] == "RUNNING"


# ---------------------------------------------------------------- /gates
def test_gates_shape():
    body = backend.build_gates(_full_ctx(), quotes_fn=_no_quotes)
    assert set(body) == {"version", "updated_at", "gate1", "gate2", "gate3"}
    assert body["version"] == "v1-20260914"
    for key, name in (("gate1", "情绪门"), ("gate2", "持仓门"), ("gate3", "选股门")):
        assert body[key]["name"] == name
        assert "state" in body[key] and "data_asof" in body[key] and "data_stale" in body[key]
    # 涨停 82 / 跌停 9 → 情绪门 GREEN（绝对阈值口径）
    assert body["gate1"]["state"] == "GREEN"
    # 持仓门：real_positions 有票但 positions.json 无 exit_watch → 无参与项 → UNKNOWN
    assert body["gate2"]["state"] == "UNKNOWN"
    # 选股门：buy_signal.db 不可用 → 如实报未产出
    assert body["gate3"]["state"] == "UNKNOWN"


def test_gates_emotion_red_threshold():
    intra = json.loads(json.dumps(INTRADAY))
    intra["market_breadth"].update({"limit_up": 12, "limit_down": 30})
    body = backend.build_gates(_full_ctx(panels={"intraday": intra}), quotes_fn=_no_quotes)
    assert body["gate1"]["state"] == "RED"
    assert "退潮加速" in body["gate1"]["reason"]


def test_gates_emotion_unknown_without_breadth():
    body = backend.build_gates(_full_ctx(drop=("intraday",)), quotes_fn=_no_quotes)
    assert body["gate1"]["state"] == "UNKNOWN"
    assert "不编造" in body["gate1"]["reason"]


def test_gates_holding_gate_uses_real_positions_and_exit_watch():
    """持仓门：实盘权威源出票 + positions.json 按 ts_code 关联 exit_watch 触发线。"""
    positions = json.loads(json.dumps(POSITIONS))
    positions["holdings"] = [{"ts_code": "512480.SH", "name": "半导体ETF",
                              "exit_watch": {"active": True, "trigger_levels": [
                                  {"level_type": "STOP_LOSS", "price": 0.80,
                                   "armed": True, "note": "保本"}]}}]
    ctx = _full_ctx(panels={"positions": positions},
                    states={"engine_state": dict(ENGINE_STATE, trade_mode="live")})

    def _quotes(_codes):
        return {"512480.SH": {"price": 0.79, "source": "watchlist_quotes"}}

    body = backend.build_gates(ctx, quotes_fn=_quotes)
    assert body["gate2"]["state"] == "RED", "现价 ≤ 武装止损线 → RED"
    assert body["gate2"]["items"][0]["ts_code"] == "512480.SH"
    assert body["gate2"]["items"][0]["stop_price"] == 0.80
    assert body["gate2"]["trade_mode"] == "live"


def test_gates_holding_gate_yellow_when_approaching():
    positions = json.loads(json.dumps(POSITIONS))
    positions["holdings"] = [{"ts_code": "512480.SH", "name": "半导体ETF",
                              "exit_watch": {"active": True, "trigger_levels": [
                                  {"level_type": "STOP_LOSS", "price": 0.80, "armed": True}]}}]
    ctx = _full_ctx(panels={"positions": positions},
                    states={"engine_state": dict(ENGINE_STATE, trade_mode="live")})
    body = backend.build_gates(
        ctx, quotes_fn=lambda _c: {"512480.SH": {"price": 0.805, "source": "s"}})
    assert body["gate2"]["state"] == "YELLOW"


def test_gates_holding_gate_follows_trade_mode_paper():
    """trade_mode=paper → 持仓权威源换成纸面账本（旧 8800 `resolve_holdings` paper 分支）。

    回归 2026-10-24：`_resolve_holdings` 曾把 mode 写死成 `live`（理由“engine_state 不可读”
    已过时），导致 paper 模式下持仓门仍读 `real_positions.json`，与 8800 在
    持仓来源 / data_basis / data_stale 三处全不一致。
    """

    class _PaperSvc:
        def account_view(self):
            return {"trade_mode": "paper", "updated_at": "2026-10-24 15:00:00",
                    "positions": {"512480.SH": {"qty": 100, "cost_price": 0.75}}}

        def account(self):
            return {"trade_date": "2026-10-24", "updated_at": "2026-10-24 15:00:00"}

    positions = json.loads(json.dumps(POSITIONS))
    positions["holdings"] = [{"ts_code": "512480.SH", "name": "半导体ETF",
                              "exit_watch": {"active": True, "trigger_levels": [
                                  {"level_type": "STOP_LOSS", "price": 0.70, "armed": True}]}}]
    ctx = _full_ctx(panels={"positions": positions},
                    states={"engine_state": dict(ENGINE_STATE, trade_mode="paper")})
    ctx.paper_override = _PaperSvc()
    body = backend.build_gates(ctx, quotes_fn=lambda _c: {"512480.SH": {"price": 0.79, "source": "s"}})
    g2 = body["gate2"]
    assert g2["trade_mode"] == "paper"
    assert g2["holdings_source"] == "paper_account.json", "paper 模式的权威源必须是纸面账本"
    codes = [it["ts_code"] for it in g2["items"]]
    assert codes == ["512480.SH"], "只有纸面账本里的票进持仓门，实盘 real_positions 不得混入"
    assert g2["data_basis"] == "trade_date", "paper 模式 stale 判据用 trade_date（旧端同口径）"


def test_gates_holding_gate_unknown_when_paper_book_unavailable():
    """paper 模式但账本读不到 → 空 items + 不编造（不回落实盘）。"""
    ctx = _full_ctx(states={"engine_state": dict(ENGINE_STATE, trade_mode="paper")})
    body = backend.build_gates(ctx, quotes_fn=_no_quotes)   # FakeCtx.paper 抛 PermissionError
    g2 = body["gate2"]
    assert g2["trade_mode"] == "paper"
    assert g2["items"] == []


def test_trade_mode_falls_back_to_live_on_bad_value():
    """非法/缺失 trade_mode → live（旧端 `mode = ... else \"live\"` 同款保守默认）。"""
    for bad in ("", None, "LIVE", "dryrun", 123):
        ctx = _full_ctx(states={"engine_state": dict(ENGINE_STATE, trade_mode=bad)})
        assert backend._trade_mode(ctx) == "live", bad
    # engine_state 整个缺失 → 也是 live
    assert backend._trade_mode(_full_ctx(drop_states=("engine_state",))) == "live"


# ---------------------------------------------------------------- /alerts
def test_alerts_shape():
    body = backend.build_alerts(
        _full_ctx(states={"engine_state": dict(ENGINE_STATE, trade_mode="live")}),
        quotes_fn=_no_quotes)
    assert set(body) == {"version", "updated_at", "alerts", "worst", "near_triggers",
                         "pnl", "source_health", "holdings"}
    assert body["version"] == "v1-20260912"
    assert set(body["near_triggers"]) >= {"state", "items"}
    assert set(body["pnl"]) == {"state", "items"}
    assert set(body["source_health"]) == {"state", "subs"}
    assert set(body["holdings"]) == {"trade_mode", "source", "source_missing",
                                     "labels_available", "label_stale"}
    for sub in body["source_health"]["subs"]:
        assert set(sub) == {"source", "state", "reason"}


def test_alerts_holding_line_alert_has_legacy_keys():
    positions = json.loads(json.dumps(POSITIONS))
    positions["holdings"] = [{"ts_code": "512480.SH", "name": "半导体ETF",
                              "exit_watch": {"active": True, "trigger_levels": [
                                  {"level_type": "STOP_LOSS", "price": 0.90, "armed": True}]}}]
    body = backend.build_alerts(
        _full_ctx(panels={"positions": positions},
                  states={"engine_state": dict(ENGINE_STATE, trade_mode="live")}),
        quotes_fn=lambda _c: {"512480.SH": {"price": 0.70, "source": "watchlist_quotes"}})
    hit = [a for a in body["alerts"] if a["source"] == "holding_line"]
    assert hit, "现价破武装止损线必须进告警"
    assert set(hit[0]) == {"level", "source", "tab", "ts_code", "name", "message"}
    assert hit[0]["level"] == "RED" and hit[0]["tab"] == "battle_plan"
    assert body["worst"] == "RED"


def test_alerts_near_triggers_shape():
    body = backend.build_alerts(
        _full_ctx(), quotes_fn=lambda _c: {"512480.SH": {"price": 1.005, "source": "s"}})
    items = body["near_triggers"]["items"]
    assert items, "有 stop_loss 的预案必须参与比对"
    assert set(items[0]) == {"ts_code", "name", "plan", "action_type", "trigger_price", "price",
                             "price_source", "distance_pct", "state", "triggered",
                             "inferred_status"}
    assert items[0]["state"] == "YELLOW", "距线 0.5% < 1% → YELLOW"


def test_alerts_source_degrade_when_intraday_missing():
    body = backend.build_alerts(_full_ctx(drop=("intraday",)), quotes_fn=_no_quotes)
    degrade = [a for a in body["alerts"] if a["source"] == "source_degrade"]
    assert degrade and degrade[0]["level"] == "RED"
    assert "intraday.json 缺失" in degrade[0]["message"]


# ---------------------------------------------------------------- /real_positions
def test_real_positions_shape_and_positions_list():
    body = backend.build_real_positions(
        _full_ctx(), quotes_fn=lambda _c: {"512480.SH": {"price": 0.836,
                                                         "prev_close": 0.83,
                                                         "source": "watchlist_quotes",
                                                         "asof": "2026-10-23 15:29:45"}})
    # 原字段全保留
    for key in ("meta", "status", "updated_at", "positions", "account"):
        assert key in body
    assert "positions_list" in body and "market_value_total" in body
    row = body["positions_list"][0]
    assert set(row) == {"ts_code", "name", "qty", "available_qty", "cost_price",
                        "last_price", "price_source", "quote_asof", "pnl_amount", "pnl_pct",
                        "day_pnl_amount", "day_pnl_pct", "market_value", "weight_pct",
                        "opened_at"}
    assert row["last_price"] == 0.836
    assert row["market_value"] == round(100 * 0.836, 2)
    assert body["market_value_total"] == row["market_value"]
    assert row["weight_pct"] == 100.0


def test_real_positions_empty_has_no_market_value_total():
    """旧端口径：positions 为空时**不多给** market_value_total 这个键。"""
    rp = json.loads(json.dumps(REAL_POSITIONS))
    rp["positions"] = []
    body = backend.build_real_positions(_full_ctx(panels={"real_positions": rp}),
                                        quotes_fn=_no_quotes)
    assert body["positions_list"] == []
    assert "market_value_total" not in body


def test_real_positions_price_falls_back_to_cost():
    """缺实时价 → 回退成本价并标 cost_fallback（不编造行情）。"""
    body = backend.build_real_positions(_full_ctx(), quotes_fn=_no_quotes)
    row = body["positions_list"][0]
    assert row["price_source"] == "cost_fallback"
    assert row["last_price"] == 1.061
    assert row["day_pnl_amount"] is None


def test_real_positions_missing_file_404():
    res = _client(_full_ctx(drop=("real_positions",))).get("/api/real_positions")
    assert res.status_code == 404
    assert "detail" in res.json()


# ---------------------------------------------------------------- /search/all
def test_search_empty_query():
    body = _client(_full_ctx()).get("/api/search/all?q=").json()
    assert body == {"q": "", "stocks": [], "sectors": []}


def test_search_by_code_and_shape():
    # 2026-10-28 口径变更：纯数字 ≥3 位按**后 3 位**匹配（主人定），故用 000 而非 6000
    body = _client(_full_ctx()).get("/api/search/all?q=000").json()
    assert set(body) == {"q", "stocks", "sectors"}
    codes = [s["code"] for s in body["stocks"]]
    assert "600000.SH" in codes
    assert set(body["stocks"][0]) == {"code", "name", "kind"}
    assert body["stocks"][0]["kind"] == "股票"


def test_search_kind_classification():
    body = _client(_full_ctx()).get("/api/search/all?q=480").json()
    kinds = {s["code"]: s["kind"] for s in body["stocks"]}
    assert kinds.get("512480.SH") == "ETF"


def test_search_bj_suffix_priority():
    """920 段必须判 BJ，不能被 9→SH 规则吃掉。"""
    body = _client(_full_ctx()).get("/api/search/all?q=920992").json()
    assert [s["code"] for s in body["stocks"]] == ["920992.BJ"]


def test_search_by_name():
    body = _client(_full_ctx()).get("/api/search/all?q=宁德").json()
    assert [s["code"] for s in body["stocks"]] == ["300750.SZ"]


def test_search_post_body_also_supported():
    """旧前端用 POST + JSON body（中文走 query 会乱码），兼容层两个方法都接。"""
    res = _client(_full_ctx()).post("/api/search/all", json={"q": "宁德", "limit": 5})
    assert res.status_code == 200
    assert res.json()["q"] == "宁德"
    assert [s["code"] for s in res.json()["stocks"]] == ["300750.SZ"]


def test_search_respects_limit():
    body = _client(_full_ctx()).get("/api/search/all?q=5&limit=1").json()
    assert len(body["stocks"]) <= 1


def test_search_without_name_table_returns_empty_not_error():
    ctx = _full_ctx()
    ctx.json_files = {}
    body = _client(ctx).get("/api/search/all?q=600000").json()
    assert body["stocks"] == []


# ---------------------------------------------------------------- 只读边界
@pytest.mark.parametrize("path", [
    "/api/dashboard.json", "/api/battle_plan.json",
    "/api/positions.json", "/api/gates", "/api/status",
    "/api/real_positions", "/api/alerts",
    "/api/alert_plan.json", "/api/push_types.json",
])
def test_no_write_routes(path):
    """compat 只有 GET：写方法一律 405（除 search/all 的 POST 是只读检索）。"""
    assert _client(_full_ctx()).post(path, json={}).status_code == 405


def test_all_endpoints_are_get_only_handlers():
    """除 /search/all 与 /shell/search 两个**只读** POST 之外，不得有任何非 GET 方法。

    批次 0（2026-10-24）新增的 POST /api/shell/search 也只是检索：它同源直调 build_search，
    不写任何文件；急停/签收两个写端点**不在本插件**（走 core，见
    docs/HANDOFF_20261024_fe_batch0.md §2）。
    """
    router = backend.register(_full_ctx())
    methods = set()
    for route in router.routes:
        methods |= set(getattr(route, "methods", set()) or set())
    assert methods <= {"GET", "POST", "HEAD"}
    posts = sorted(r.path for r in router.routes if "POST" in (getattr(r, "methods", set()) or set()))
    assert posts == ["/search/all", "/shell/search"]


def test_all_task_endpoints_registered():
    """任务书 15 个端点里，14 个在本插件（`/concept.json` 已移交 concept 插件）。"""
    router = backend.register(_full_ctx())
    paths = {r.path for r in router.routes}
    expected = {
        "/dashboard.json", "/battle_plan.json", "/amv_live.json", "/amv_regime_latest.json",
        "/position_cap_latest.json", "/alert_todo.json", "/alert_plan.json",
        "/push_types.json", "/positions.json", "/gates", "/status",
        "/real_positions", "/alerts", "/search/all",
    }
    assert expected <= paths, "缺端点：%s" % (expected - paths)


# ---------------------------------------------------------------- 外壳缺失端点（批次 0）
def test_search_matches_pinyin_and_code_anywhere():
    """2026-10-28 模糊搜索：代码任意段 / 拼音全拼 / 首字母 / 多关键词全命中（主人定）。"""
    from types import SimpleNamespace  # noqa: PLC0415 - 局部构造最小 ctx

    names = {"600519": "贵州茅台", "300750": "宁德时代"}
    py = {"贵州茅台": {"f": "guizhoumaotai", "i": "gzmt"},
          "宁德时代": {"f": "ningdeshidai", "i": "ndsd"}}
    ctx = SimpleNamespace(
        json_read=lambda n: ({"names": names} if n == "stock_names"
                             else {"items": py} if n == "pinyin_index" else {}),
        db_read=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("no db")),
    )
    for q in ("gzmt", "guizhoumaotai", "0519", "600519.SH"):
        got = backend.build_search(ctx, q)
        assert any(s["code"] == "600519.SH" for s in got["stocks"]), q
    got = backend.build_search(ctx, "茅台 519")
    assert got["stocks"][0]["code"] == "600519.SH"
    assert got["sectors"] == []
    # 2026-10-28 主人定：纯数字不足 3 位不参与代码检索；单字/单字母不参与名称/拼音
    assert backend.build_search(ctx, "51")["stocks"] == []
    assert backend.build_search(ctx, "茅")["stocks"] == []
    assert backend.build_search(ctx, "g")["stocks"] == []
    assert backend.build_search(ctx, "茅台")["stocks"][0]["code"] == "600519.SH"
    assert backend.build_search(ctx, "gzmt")["stocks"][0]["code"] == "600519.SH"
    assert backend.build_search(ctx, "519")["stocks"][0]["code"] == "600519.SH"


def test_shell_search_returns_old_shape():
    """/api/shell/search（POST {q,limit}）→ {q, stocks, sectors}，与旧壳 api_shell_search 同形。

    旧端（engine/shell_server.py）是把外壳的请求转发到 8800 /api/search/all；8810 里两者同进程，
    故同源直调 build_search —— 返回结构与旧端逐字段一致，少的是那一跳 HTTP。
    """
    body = _client(_full_ctx()).post("/api/shell/search", json={"q": "600000", "limit": 5}).json()
    assert set(body) == {"q", "stocks", "sectors"}
    assert body["q"] == "600000"
    assert body["stocks"][0] == {"code": "600000.SH", "name": "浦发银行", "kind": "股票"}


def test_shell_search_covers_etf_name_table() -> None:
    """ETF 也要搜得到（2026-11-16 主人报「侧栏搜索搜不到 ETF」）。

    名表把 ETF **单独放 `etfs` 段**（`names` 段 5582 只全是个股，5/1 开头一只没有），
    旧实现只遍历 `names`，于是 510300 / 沪深300ETF 全空。这里钉住：两段都进候选、
    kind 按号段标 ETF、个股不受影响。
    """
    from types import SimpleNamespace  # noqa: PLC0415 - 局部构造最小 ctx

    names = {"600519": "贵州茅台"}
    etfs = {"510300": "沪深300ETF华泰柏", "159915": "创业板ETF易方达"}
    ctx = SimpleNamespace(
        json_read=lambda n: ({"names": names, "etfs": etfs} if n == "stock_names" else {}),
        db_read=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("no db")),
    )
    by_code = {s["code"]: s for s in backend.build_search(ctx, "510300", 8)["stocks"]}
    assert by_code["510300.SH"] == {
        "code": "510300.SH", "name": "沪深300ETF华泰柏", "kind": "ETF"}
    assert any(s["code"] == "159915.SZ"
               for s in backend.build_search(ctx, "创业板ETF", 8)["stocks"])
    assert backend.build_search(ctx, "茅台", 8)["stocks"][0]["code"] == "600519.SH"


def test_shell_search_sector_code_lands_in_stocks_with_kind():
    """同花顺板块指数（880xxx）经名字表命中时落 stocks 段且 kind=板块。

    旧壳搜索面板按 kind 给每行打标签；真正来自 amv_board.db 的板块走 sectors 段，
    但测试沙箱无库（ctx.db_read 抛 FileNotFoundError）→ sectors 必为空，不能在这里编造。
    """
    body = _client(_full_ctx()).post("/api/shell/search", json={"q": "880005"}).json()
    codes = [x["code"] for x in body["stocks"]]
    assert "880005.SH" in codes
    assert body["stocks"][0]["kind"] == "板块"
    assert body["sectors"] == []


def test_shell_search_empty_body_is_safe():
    """空体 / 非 JSON body → 空结果，不抛 500（旧壳输入框清空时也会打这个端点）。"""
    client = _client(_full_ctx())
    assert client.post("/api/shell/search", json={}).json() == {"q": "", "stocks": [], "sectors": []}
    assert client.post("/api/shell/search").json() == {"q": "", "stocks": [], "sectors": []}


def test_shell_search_never_opens_http_connection():
    """本插件不得代理 HTTP（静态扫描口径：urlopen/subprocess 都在 cutover_gate 禁令里）。"""
    source = (PLUGIN_DIR / "backend.py").read_text(encoding="utf-8")
    for token in ("urllib", "requests.", "httpx", "subprocess.", "Popen("):
        assert token not in source, token


def test_task_endpoints_include_shell_search():
    """批次 0 新增端点已注册到 root（完整路径 /api/shell/search）。"""
    paths = {r.path for r in backend.register(_full_ctx()).routes}
    assert "/shell/search" in paths


# ---------------------------------------------------------------- 0.2.0 通配 {name}.json


def test_wildcard_registered_last():
    """`/{name}.json` 通配必须**最后注册**（具体路由在前，通配在后）。"""
    router = backend.register(_full_ctx())
    paths = [r.path for r in router.routes]
    assert "/{name}.json" in paths
    assert paths[-1] == "/{name}.json", paths[-5:]


def test_wildcard_serves_readable_panel():
    """白名单内的名字 → ctx.panel_json(name)，原样透传。"""
    ctx = _full_ctx(panels={"focus_levels": {"code": "X", "levels": [1, 2]}})
    body = _client(ctx).get("/api/focus_levels.json").json()
    assert body == {"code": "X", "levels": [1, 2]}


def test_wildcard_404_for_unknown_name():
    """白名单外 → 404（与旧端 `api_doc` 的「未知文档」同语义）。"""
    r = _client(_full_ctx()).get("/api/whatever.json")
    assert r.status_code == 404
    assert "未知文档" in r.json()["detail"]


@pytest.mark.parametrize("name", ["../secret", "..%2Fsecret", "dashboard.bak", "amv_live "])
def test_wildcard_blocks_path_traversal(name):
    """防任意文件读：名字必须以白名单键命中，插件**不做路径拼接** ⇒ 不可能 200。"""
    from urllib.parse import quote
    r = _client(_full_ctx()).get("/api/%s.json" % quote(name, safe=""))
    assert r.status_code in (404, 400, 307), (name, r.status_code, r.text[:80])


def test_wildcard_whitelist_is_core_readable_panel():
    """白名单唯一源 = core `paths.READABLE_PANEL`（插件不自建第二份名单）。"""
    from app.core import paths as core_paths
    assert backend._PANEL_JSON_NAMES == frozenset(core_paths.READABLE_PANEL)


def test_wildcard_missing_file_is_404_not_500():
    """名字在白名单但磁盘无文件 → 404（不装 200 空对象）。"""
    r = _client(_full_ctx()).get("/api/selection_pool.json")
    assert r.status_code == 404


def test_wildcard_does_not_shadow_specific_routes():
    """**核心回归**：多个 root 插件共存时，通配不得抢走后面插件的具体 `.json` 路由。

    复现条件（真实存在）：compat 按目录名排序先于 compat_sector / concept / ops 加载，
    它们的 `/api/sector_card.json`、`/api/concept.json`、`/api/ops.json` 都是字面路径。
    若通配先匹配并接管，这些端点会集体 404 —— 实测过，所以这里锁死。
    """
    from fastapi import APIRouter, FastAPI

    later = APIRouter()

    @later.get("/sector_card.json")
    def sector_card():
        return {"who": "sector"}

    @later.get("/concept.json")
    def concept():
        return {"who": "concept"}

    app = FastAPI()
    app.include_router(backend.register(_full_ctx()), prefix=PREFIX)   # 通配在前
    app.include_router(later, prefix=PREFIX)                            # 具体路由在后
    c = TestClient(app)
    assert c.get("/api/sector_card.json").json() == {"who": "sector"}
    assert c.get("/api/concept.json").json() == {"who": "concept"}
    assert c.get("/api/dashboard.json").json() == DASHBOARD


# ---------------------------------------------------------------- 0.2.0 amv_research


def _rs_ctx(tmp_path, *, amv=None, state=None, total=None, board=None, members=None,
            concept=None, concept_members=None, names=None, amv_live=None):
    """造一个含 amv_board.db + ths_board.db + 口径库的真实 ctx（不碰生产库）。"""
    import importlib.util
    import sqlite3

    from app.core import paths as core_paths

    amv_db = tmp_path / "amv_board.db"
    con = sqlite3.connect(str(amv_db))
    con.execute("CREATE TABLE stock_amv(date TEXT, stock TEXT, amv REAL)")
    con.execute("CREATE TABLE stock_state(stock TEXT, date TEXT, alive REAL, sh REAL)")
    con.execute("CREATE TABLE daily_total(date TEXT, total REAL)")
    con.executemany("INSERT INTO stock_amv VALUES(?,?,?)", amv or [])
    con.executemany("INSERT INTO stock_state VALUES(?,?,?,?)", state or [])
    con.executemany("INSERT INTO daily_total VALUES(?,?)", total or [])
    con.commit()
    con.close()

    ths_db = tmp_path / "ths_board.db"
    con = sqlite3.connect(str(ths_db))
    con.execute("CREATE TABLE board(code TEXT, name TEXT, sw_level INTEGER)")
    con.execute("CREATE TABLE board_member(board_code TEXT, stock TEXT)")
    con.execute("CREATE TABLE concept(code TEXT, name TEXT)")
    con.execute("CREATE TABLE concept_member(concept_code TEXT, stock TEXT)")
    con.executemany("INSERT INTO board VALUES(?,?,?)", board or [])
    con.executemany("INSERT INTO board_member VALUES(?,?)", members or [])
    con.executemany("INSERT INTO concept VALUES(?,?)", concept or [])
    con.executemany("INSERT INTO concept_member VALUES(?,?)", concept_members or [])
    con.commit()
    con.close()

    class RsCtx(FakeCtx):
        def db_read(self, name):
            path = {"amv_board.db": amv_db, "ths_board.db": ths_db}.get(name)
            if path is None:
                raise FileNotFoundError(name)
            self.db_calls.append(name)
            uri = "file:%s?mode=ro" % str(path).replace("\\", "/")
            con = sqlite3.connect(uri, uri=True)
            con.row_factory = sqlite3.Row
            return con

        def lib(self, name):
            spec_path = core_paths.lib_path(name)
            if spec_path is None:
                raise PermissionError(name)
            spec = importlib.util.spec_from_file_location("_t_" + name, spec_path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules["_t_" + name] = mod
            parent = str(spec_path.parent)
            inserted = parent not in sys.path
            if inserted:
                sys.path.append(parent)
            try:
                spec.loader.exec_module(mod)
            finally:
                if inserted:
                    sys.path.remove(parent)
            return mod

    ctx = RsCtx(json_files={"stock_names": names or {"600000": "浦发银行"}})
    # 2026-10-24：`ctx.amv_live` 是实时口径门面（core `app/core/amvlive.py`）。
    # 默认给一个**不可用**的替身（snapshot() 恒 None + last_error 有原因），
    # 让所有存量用例继续走收盘口径；要测实时分支的用例显式传 `amv_live=...` 覆盖。
    ctx.amv_live = amv_live if amv_live is not None else _UnavailableAmvLive()
    return ctx


def _rs_fixture(tmp_path, days=5):
    """两个板块 × 两只票 × N 日的自造库；数值可手算。"""
    dates = ["2026-10-%02d" % d for d in range(1, days + 1)]
    amv, state, total = [], [], []
    for i, d in enumerate(dates):
        total.append((d, 1000.0 + i * 100.0))
        for s in ("600000", "000001"):
            amv.append((d, s, 100.0 + i * 10.0))
            state.append((s, d, 10.0, 20.0))
    return {
        "amv": amv, "state": state, "total": total,
        "board": [("881101.TI", "种植业", 2), ("881102.TI", "养殖业", 2)],
        "members": [("881101.TI", "600000"), ("881101.TI", "000001"),
                    ("881102.TI", "600000")],
    }


def _fresh_rs_cache():
    """每个用例清缓存：模块级 _RS_CACHE 跨用例残留会让断言读到上一条结果。"""
    backend._RS_CACHE.clear()


class _UnavailableAmvLive:
    """`ctx.amv_live` 替身：恒不可用（QMT 未连 / 盘外）。"""

    def snapshot(self):
        return None

    def last_error(self):
        return "empty"


class _FakeAmvLive:
    """`ctx.amv_live` 替身：返回一张手算过的快照（日期可指定）。"""

    def __init__(self, doc):
        self.doc = doc
        self.calls = 0

    def snapshot(self):
        self.calls += 1
        return self.doc

    def last_error(self):
        return None


def test_amv_research_shape_and_defaults(tmp_path):
    """默认参：level=L2、days=30、scope=board；返回键与旧端逐字一致。"""
    _fresh_rs_cache()
    fx = _rs_fixture(tmp_path, days=5)
    c = _client(_rs_ctx(tmp_path, **fx))
    body = c.get("/api/amv_research.json").json()
    assert body["ok"] is True
    assert set(body) == {
        "ok", "asof", "prev", "live_mode", "live_date", "live_err", "level", "level_label",
        "n", "total_pct", "note", "boards", "sel", "sel_name", "sel_contrib",
        "n_members", "sel_parts", "members", "scope",
    }, set(body)
    assert body["level"] == "L2" and body["level_label"] == "同花顺二级"
    assert body["scope"] == "board"
    assert body["live_mode"] is False and body["live_err"] is None
    assert body["asof"] == "2026-10-05" and body["prev"] == "2026-10-04"
    assert body["n"] == 2


def test_amv_research_all_scope_has_all_parts(tmp_path):
    """全A 视图的 AMV 贡献分项（2026-11-06 主人）：scope=all 追加 all_parts 且合计实算。

    口径与板块分项同一套：四档（px/sh/act/new/susp）+ 大盘基准（全A 以自己为分母）。
    生产数据里明细（stock_amv）与 daily_total 对齐时合计自然为 0‰；本 fixture 总量与两只票
    不自洽，所以这里只锁「合计 = 六项之和（不硬编码 0）」。board 模式不许多这个键。
    """
    _fresh_rs_cache()
    fx = _rs_fixture(tmp_path)
    c = _client(_rs_ctx(tmp_path, **fx))
    body = c.get("/api/amv_research.json?scope=all&code=881102.TI").json()
    assert body["ok"] is True and body["scope"] == "all"
    ap = body.get("all_parts")
    assert ap is not None, "scope=all 必须带 all_parts（全A 视图分项块的数据源）"
    assert set(ap) == {"px", "sh", "act", "new", "susp", "den", "total", "n_calc"}
    parts = [ap[k] for k in ("px", "sh", "act", "new", "susp", "den")]
    assert ap["total"] == round(sum(parts), 3), "合计必须实算（= 六项之和），不硬编码 0"
    assert ap["n_calc"] == 2, "fixture 两只票都参与"
    # 不能是板块分项的改头换面：至少有一个分项与 sel_parts 不同
    sp = body.get("sel_parts") or {}
    assert sp and any(ap[k] != sp.get(k) for k in ("px", "sh", "act", "new")), \
        "all_parts 必须是全A 汇总，不是 sel_parts 原样搬运"

    # board 模式键集必须与旧端逐字一致（不许多 all_parts）
    _fresh_rs_cache()
    b2 = c.get("/api/amv_research.json").json()
    assert "all_parts" not in b2


def test_amv_research_both_paths_identical(tmp_path):
    """双路径 `/api/amv_research` 与 `/api/amv_research.json` 响应一致。"""
    _fresh_rs_cache()
    fx = _rs_fixture(tmp_path)
    c = _client(_rs_ctx(tmp_path, **fx))
    a = c.get("/api/amv_research.json?level=L2&days=20").json()
    b = c.get("/api/amv_research?level=L2&days=20").json()
    assert a == b
    assert a["ok"] is True


def test_amv_research_param_clamping(tmp_path):
    """参数钳位：非法 level → L2、scope 非 all → board。

    注意 `live=abc` 旧端就是 **422**（FastAPI `int` 注解解析失败），本实现同口径，
    所以不要指望它被默默当 0。
    """
    _fresh_rs_cache()
    fx = _rs_fixture(tmp_path)
    c = _client(_rs_ctx(tmp_path, **fx))
    body = c.get("/api/amv_research.json?level=XX&days=999&scope=zzz").json()
    assert body["level"] == "L2"
    assert body["scope"] == "board"
    assert body["live_err"] is None, "未传 live 时不该出现回退说明"
    assert body["ok"] is True
    assert c.get("/api/amv_research.json?days=1").json()["ok"] is True
    assert c.get("/api/amv_research.json?live=abc").status_code == 422, "与旧端同口径"


def test_amv_research_live_degrades_with_note(tmp_path):
    """live=1 但实时门面不可用 → live_mode=false + live_err 有原因（前端自动回退收盘口径）。"""
    _fresh_rs_cache()
    fx = _rs_fixture(tmp_path)
    c = _client(_rs_ctx(tmp_path, **fx))          # 默认 ctx.amv_live = _UnavailableAmvLive
    body = c.get("/api/amv_research.json?live=1").json()
    assert body["live_mode"] is False
    assert body["live_date"] is None
    assert body["live_err"] and "empty" in body["live_err"]


def test_amv_research_live_official_equals_close_when_unavailable(tmp_path):
    """实时拿不到时，live=1 与 live=0 必须逐字段相等（除 live_* 三个键）——不拿半截快照凑数。"""
    _fresh_rs_cache()
    fx = _rs_fixture(tmp_path)
    c = _client(_rs_ctx(tmp_path, **fx))
    close = c.get("/api/amv_research.json").json()
    live = c.get("/api/amv_research.json?live=1").json()
    for k in ("ok", "asof", "prev", "level", "level_label", "n", "total_pct",
              "boards", "sel", "sel_name", "sel_contrib", "n_members",
              "sel_parts", "members", "scope", "note"):
        assert live[k] == close[k], k


def test_amv_research_live_uses_snapshot_when_same_day_gt_d1(tmp_path):
    """快照日 > d1（EOD 还没含当日）→ 基期 d1、分母 T1，分子取快照 AMV。

    自造数据：`_rs_fixture(days=5)` → 日期 10-01..10-05，d1=10-05 为最后一根。
    每只票每日 AMV = 100 + i*10，所以 d1 末值 140；881101 两只票的板块末值 = 280。
    快照日 10-06 > d1 ⇒ 基期 d1（s0=280），分子用快照（120+30=150）。
    """
    _fresh_rs_cache()
    fx = _rs_fixture(tmp_path, days=5)
    snap = {"date": "2026-10-06", "asof": "14:30:00", "all_amv": 1500.0,
            "stocks": {"600000": [120.0, 10.0, 20.0, 1.0, 1.0],
                        "000001": [30.0, 10.0, 20.0, 1.0, 1.0]}}
    c = _client(_rs_ctx(tmp_path, **fx, amv_live=_FakeAmvLive(snap)))
    body = c.get("/api/amv_research.json?live=1").json()
    assert body["live_mode"] is True
    assert body["live_date"] == "2026-10-06"
    assert body["asof"] == "14:30:00"
    assert body["prev"] == "2026-10-05"          # _use_d1 → 基期 d1
    # amv_pct = s1/s0 - 1，s0 = 板块在 d1 的末值（280），s1 = 快照聚合（150）
    bb = {r["c"]: r for r in body["boards"]}
    assert bb["881101.TI"]["amv_pct"] == round((150.0 / 280.0 - 1.0) * 100.0, 2)
    # 贡献同理用快照的 all_amv（1500）作分母，而不是 EOD 的 T1（1400）
    assert bb["881101.TI"]["contrib"] == round((150.0 / 1500.0 - 280.0 / 1400.0) * 1000.0, 3)


def test_amv_research_live_falls_back_when_snapshot_older_than_d1(tmp_path):
    """快照日 < d1（EOD 已更新得更新）→ 不采信快照，与收盘口径逐字段相等。"""
    _fresh_rs_cache()
    fx = _rs_fixture(tmp_path, days=5)
    snap = {"date": "2026-10-01", "asof": "14:30:00", "all_amv": 999.0,
            "stocks": {"600000": [1.0, 1.0, 1.0, 1.0, 1.0]}}
    c = _client(_rs_ctx(tmp_path, **fx, amv_live=_FakeAmvLive(snap)))
    body = c.get("/api/amv_research.json?live=1").json()
    assert body["live_mode"] is False           # 旧快照不充实时
    assert body["live_date"] is None
    close = c.get("/api/amv_research.json").json()
    assert body["boards"] == close["boards"]


def test_amv_research_live_cache_key_separated(tmp_path):
    """live 与 收盘各一份缓存：live=1 不把收盘结果回写到 live 请求上，反之亦然。"""
    _fresh_rs_cache()
    fx = _rs_fixture(tmp_path, days=5)
    fake = _FakeAmvLive({"date": "2026-10-06", "asof": "14:30:00", "all_amv": 1500.0,
                         "stocks": {"600000": [120.0, 10.0, 20.0, 1.0, 1.0]}})
    c = _client(_rs_ctx(tmp_path, **fx, amv_live=fake))
    close = c.get("/api/amv_research.json").json()
    live = c.get("/api/amv_research.json?live=1").json()
    assert close["live_mode"] is False and live["live_mode"] is True
    assert backend.RS_TTL_LIVE == 20.0           # 实时 20s（旧端同值）
    assert backend.RS_TTL > backend.RS_TTL_LIVE


def test_amv_research_live_same_date_as_d1_uses_d0_base(tmp_path):
    """快照日 == d1（EOD 已含当日）→ 基期回 d0、分母 T0（旧端 2026-10-23 修的当日双算）。

    自造数据 10-01..10-05，d0=10-04（AMV 130）、d1=10-05（140）、T0=1300、T1=1400。
    快照日 10-05 == d1 ⇒ `_use_d1` 假 → 基期 d0，但分子仍取快照（live_mode=true）。
    这一条是「盘中 EOD 先跑完」的真实场景：若基期错写成 d1，每只票 amv_pct≈0、全榜变负。
    """
    _fresh_rs_cache()
    fx = _rs_fixture(tmp_path, days=5)
    snap = {"date": "2026-10-05", "asof": "14:30:00", "all_amv": 1600.0,
            "stocks": {"600000": [150.0, 10.0, 20.0, 1.0, 1.0],
                        "000001": [150.0, 10.0, 20.0, 1.0, 1.0]}}
    c = _client(_rs_ctx(tmp_path, **fx, amv_live=_FakeAmvLive(snap)))
    body = c.get("/api/amv_research.json?live=1").json()
    assert body["live_mode"] is True
    assert body["live_date"] == "2026-10-05"
    assert body["prev"] == "2026-10-04", "快照日 <= d1 ⇒ 基期 d0"
    bb = {r["c"]: r for r in body["boards"]}
    # s0 = 板块 d0 末值 260；s1 = 快照聚合 300
    assert bb["881101.TI"]["amv_pct"] == round((300.0 / 260.0 - 1.0) * 100.0, 2)
    # 分母 T_base = T0 = 1300（不是 T1=1400）
    assert bb["881101.TI"]["contrib"] == round((300.0 / 1600.0 - 260.0 / 1300.0) * 1000.0, 3)
    assert body["total_pct"] == round((1600.0 / 1300.0 - 1.0) * 100.0, 3)


def test_amv_research_live_missing_stock_counts_as_zero(tmp_path):
    """快照里缺某只票 → 该票 `_x1 = 0`（旧端同式），不当成「没变化」。"""
    _fresh_rs_cache()
    fx = _rs_fixture(tmp_path, days=5)
    # 000001 不在快照里（停牌 / 快照未覆盖）
    snap = {"date": "2026-10-06", "asof": "14:30:00", "all_amv": 1500.0,
            "stocks": {"600000": [120.0, 10.0, 20.0, 1.0, 1.0]}}
    c = _client(_rs_ctx(tmp_path, **fx, amv_live=_FakeAmvLive(snap)))
    body = c.get("/api/amv_research.json?live=1&code=881101.TI").json()
    assert body["live_mode"] is True
    mem = {m["c"]: m for m in body["members"]}
    assert "000001" in mem, "缺快照的票仍应出现在成分股表（x0>0）"
    assert mem["000001"]["amv_pct"] is None, "缺快照 ⇒ 没有当日分子，不能报 0%"
    # 板块 s1 = 只有 600000 的 120（000001 计 0）��s0 = d1 板块值 280
    bb = {r["c"]: r for r in body["boards"]}
    assert bb["881101.TI"]["amv_pct"] == round((120.0 / 280.0 - 1.0) * 100.0, 2)
    # 分项：000001 走 `x1 <= 0` 分支 → 全额「停牌/缺数」
    sp = body["sel_parts"]
    assert sp is not None and sp["susp"] != 0.0


def test_amv_research_core_without_amv_live_degrades(tmp_path):
    """老 core（无 `ctx.amv_live`）→ live_err 说明且回收盘口径，不抛 500。"""
    _fresh_rs_cache()
    fx = _rs_fixture(tmp_path, days=5)
    ctx = _rs_ctx(tmp_path, **fx)
    del ctx.amv_live                                  # 模拟 core 未提供这个门面
    c = _client(ctx)
    r = c.get("/api/amv_research.json?live=1")
    assert r.status_code == 200
    body = r.json()
    assert body["live_mode"] is False and body["live_date"] is None
    assert body["live_err"] and "ctx.amv_live" in body["live_err"]
    close = c.get("/api/amv_research.json").json()
    assert body["boards"] == close["boards"]


def test_amv_research_live_snapshot_raises_degrades(tmp_path):
    """门面 `snapshot()` 抛异常 → live_err 带异常类型，回收盘口径，不抛 500。"""
    _fresh_rs_cache()
    fx = _rs_fixture(tmp_path, days=5)

    class _Boom:
        def snapshot(self):
            raise RuntimeError("qmt 掉线")

        def last_error(self):
            raise AssertionError("不该再问 last_error")

    c = _client(_rs_ctx(tmp_path, **fx, amv_live=_Boom()))
    r = c.get("/api/amv_research.json?live=1")
    assert r.status_code == 200
    body = r.json()
    assert body["live_mode"] is False
    assert "RuntimeError" in body["live_err"] and "qmt 掉线" in body["live_err"]


def test_amv_research_concept_note(tmp_path):
    """CONCEPT：note 追加「不可相加」提示（旧端同款）。"""
    _fresh_rs_cache()
    fx = _rs_fixture(tmp_path)
    fx["concept"] = [("885431.TI", "新能源汽车")]
    fx["concept_members"] = [("885431.TI", "600000")]
    c = _client(_rs_ctx(tmp_path, **fx))
    body = c.get("/api/amv_research.json?level=CONCEPT").json()
    assert body["level"] == "CONCEPT" and body["level_label"] == "自建"
    assert "不可相加" in body["note"]


def test_amv_research_bj_members_excluded(tmp_path):
    """板块池剔北交（与 amv_live 聚合 mask 同源）⇒ Σ成分股 == 板块值。"""
    _fresh_rs_cache()
    fx = _rs_fixture(tmp_path)
    fx["members"] = [("881101.TI", "600000"), ("881101.TI", "920087")]
    for d, _s, _a in list(fx["amv"]):
        if _s == "600000":
            fx["amv"].append((d, "920087", 50.0))
    c = _client(_rs_ctx(tmp_path, **fx))
    body = c.get("/api/amv_research.json?code=881101.TI").json()
    member_codes = {m["c"] for m in body["members"]}
    assert "920087" not in member_codes, "北交票必须从成分股表剔除"
    assert "600000" in member_codes


def test_amv_research_selection_falls_back_to_top(tmp_path):
    """`code` 不存在于池中 → 回退贡献第一名。"""
    _fresh_rs_cache()
    fx = _rs_fixture(tmp_path)
    c = _client(_rs_ctx(tmp_path, **fx))
    body = c.get("/api/amv_research.json?code=999999.XX").json()
    assert body["sel"] == body["boards"][0]["c"]


def test_amv_research_scope_all_caps_members(tmp_path):
    """scope=all → 全A个股榜，成员数受 RS_ALL_CAP 约束，n_members 报真实总数。"""
    _fresh_rs_cache()
    fx = _rs_fixture(tmp_path)
    c = _client(_rs_ctx(tmp_path, **fx))
    body = c.get("/api/amv_research.json?scope=all").json()
    assert body["scope"] == "all"
    assert len(body["members"]) <= backend.RS_ALL_CAP
    assert body["n_members"] == 2


def test_amv_research_sel_parts_identity(tmp_path):
    """分项恒等式：五项 + 大盘基准 = 指数贡献（sel_parts.cover == total，逐分钱对得上）。"""
    _fresh_rs_cache()
    fx = _rs_fixture(tmp_path)
    c = _client(_rs_ctx(tmp_path, **fx))
    body = c.get("/api/amv_research.json").json()
    sp = body["sel_parts"]
    assert sp is not None
    parts_sum = sp["px"] + sp["sh"] + sp["act"] + sp["new"] + sp["susp"] + sp["den"]
    assert abs(parts_sum - sp["total"]) < 1e-3, (parts_sum, sp["total"])
    assert abs(sp["cover"] - sp["total"]) < 1e-3


def test_amv_research_empty_base_returns_ok_false(tmp_path):
    """库空（无 stock_amv）→ ok=false + detail，不抛 500、不编数据。"""
    _fresh_rs_cache()
    c = _client(_rs_ctx(tmp_path, board=[], members=[]))
    body = c.get("/api/amv_research.json").json()
    assert body["ok"] is False and body["detail"]
    assert body["boards"] == [] and body["members"] == []


def test_amv_research_cache_ttl_constant():
    """缓存 TTL 从旧端抄（_RS_TTL = 120s）；成分/全A 上限与旧端一致。"""
    assert backend.RS_TTL == 120.0
    assert backend.RS_MEMBER_CAP == 100
    assert backend.RS_ALL_CAP == 300


# ---------------------------------------------------------------- 2026-11-07 盘后落盘
#
# 主人：「收盘后的数据就已经固定下来了，没必要每次都重拉重算」。
# 盘后（周末 / 15:05 后 / 09:15 前）统一走收盘口径，结果按两库 mtime+size 落盘；
# 重启后第一个请求直接读盘，不跑 _rs_base（冷启动 6.8s 的大头）。


def test_amv_research_postclose_hits_disk_after_restart(tmp_path, monkeypatch):
    _fresh_rs_cache()
    fx = _rs_fixture(tmp_path, days=5)
    ctx = _rs_ctx(tmp_path, **fx)
    monkeypatch.setattr(backend, "_rs_is_postclose", lambda *a, **k: True)

    first = backend.build_amv_research(ctx, level="L2", days=5, live=1, scope="all")
    assert "amv_research" in ctx.cache_docs, "盘后首次应落盘"
    assert ctx.cache_docs["amv_research"]["version"], "落盘应带源库数据戳"

    # 模拟进程重启：内存缓存全丢，磁盘还在
    _fresh_rs_cache()
    monkeypatch.setattr(backend, "_rs_base",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("命中磁盘时不应重算")))
    second = backend.build_amv_research(ctx, level="L2", days=5, live=1, scope="all")
    assert second == first, "磁盘命中的结果必须与首次逐字段一致"


def test_amv_research_cache_asof_mismatch_is_miss(tmp_path, monkeypatch):
    """铁律 3：缓存里的数据日与当前库最新日不一致 → 当 miss 重算，不得把昨天的当今天。"""
    _fresh_rs_cache()
    fx = _rs_fixture(tmp_path, days=5)
    ctx = _rs_ctx(tmp_path, **fx)
    monkeypatch.setattr(backend, "_rs_is_postclose", lambda *a, **k: True)
    first = backend.build_amv_research(ctx, level="L2", days=5, live=1, scope="all")
    assert first["ok"] is True and "amv_research" in ctx.cache_docs

    # 把缓存里的数据日改成“昨天”（模拟旧值）
    doc = ctx.cache_docs["amv_research"]
    for payload in doc["items"].values():
        payload["asof"] = "1999-01-01"

    _fresh_rs_cache()
    monkeypatch.setattr(backend, "_rs_base", lambda *a, **k: {"ok": False, "detail": "重算过了"})
    out = backend.build_amv_research(ctx, level="L2", days=5, live=1, scope="all")
    assert out.get("ok") is False or out.get("stale") is True, "旧日期的缓存必须不当命中"


def test_amv_research_empty_boards_not_cached(tmp_path, monkeypatch):
    """铁律 1/2：取不到榜单（ok=false）时不得落盘，避免把“无数据”固定成“有数据”。"""
    _fresh_rs_cache()
    ctx = _rs_ctx(tmp_path, board=[], members=[])
    monkeypatch.setattr(backend, "_rs_is_postclose", lambda *a, **k: True)
    out = backend.build_amv_research(ctx, level="L2", days=5, live=1, scope="all")
    assert out.get("ok") is False
    assert "amv_research" not in ctx.cache_docs, "空结果不得写缓存"


def test_amv_research_postclose_cache_invalidated_when_db_changes(tmp_path, monkeypatch):
    import sqlite3
    import time as _time

    _fresh_rs_cache()
    fx = _rs_fixture(tmp_path, days=5)
    ctx = _rs_ctx(tmp_path, **fx)
    monkeypatch.setattr(backend, "_rs_is_postclose", lambda *a, **k: True)
    first = backend.build_amv_research(ctx, level="L2", days=5, live=1, scope="all")
    assert first["ok"] is True

    # 库更新：多一个交易日
    con = sqlite3.connect(str(tmp_path / "amv_board.db"))
    con.execute("INSERT INTO stock_amv VALUES('2026-10-06','600000',160.0)")
    con.execute("INSERT INTO daily_total VALUES('2026-10-06',1600.0)")
    con.commit()
    con.close()
    _time.sleep(0.05)          # 保证 mtime 可比

    _fresh_rs_cache()
    out = backend.build_amv_research(ctx, level="L2", days=5, live=1, scope="all")
    assert out["asof"] == "2026-10-06", "库变了必须重算，不得返回旧缓存"
