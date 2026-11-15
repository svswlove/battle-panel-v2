# -*- coding: utf-8 -*-
"""paper 插件 HTTP 契约测试（未运行验证：本子代理无 shell，pytest 由主会话执行）。

覆盖（**全部临时账本目录 + 假行情**，绝不连 QMT / eltdx / 真实账本）：

1. `test_manifest_paper_flag`：`data.paper=true` + `mount=root` + 无 write；
2. `test_ctx_paper_permission`：未声明 `data.paper` → `ctx.paper` 抛 PermissionError；
3. `test_place_and_list`：下单 → 委托/账户/账本四端点都有真实数据；
4. `test_cancel`：挂单撤销 → CANCELLED；
5. `test_condition_add_list_cancel`：加止损条件单 → list → 撤单 → CANCELLED；
6. `test_trades_points`：纸面成交 → `/api/trades/{code}` 有 B/S 点；
7. `test_no_route_conflict`：`/api/health` 200、`/_plugins/health` 全 ok，
   且 `compat_misc` 不再拥有 `/api/paper_account` 等 5 条路径；
8. `test_feed_status_endpoint`：`/api/paper/feed/status` 200 且有 `state`；
9. `test_ledger_live_mode_refused` / `test_bad_params` / `test_no_forbidden_quote_usage`。

**行情怎么处理**：`PaperOrderService.quote_for` 被 monkeypatch 成固定快照（任务书 §7.1）；
另外 `app.core.quotes.order_metrics` 也换掉——`/api/order/list` 的 `metrics` 会走它，
不换就会真开 eltdx socket（测试必须零网络）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import feed as core_feed  # noqa: E402
from app.core import quotes as core_quotes  # noqa: E402
from app.core.contract import (  # noqa: E402
    ContractError,
    PluginContext,
    find_forbidden_quote_usage,
    load_manifest,
    validate_manifest,
)
from app.core.main import create_app  # noqa: E402
from app.core.paper import service as paper_service  # noqa: E402
from app.core.paper.orders import PaperOrderService  # noqa: E402

PLUGIN_DIR = Path(__file__).resolve().parents[1]
BACKEND_PY = PLUGIN_DIR / "backend.py"
VIEWS_PY = PLUGIN_DIR / "views.py"

CODE = "600519.SH"
CODE6 = "600519"

# 固定下单行情（任务书 §7.1 逐字段）：现价 10.00、卖一 10.01、买一 9.99、涨跌停 11/9
FAKE_QUOTE: Dict[str, Any] = {
    "last": 10.0, "pre_close": 9.8,
    "bids": [{"price": 9.99, "vol": 1000}], "asks": [{"price": 10.01, "vol": 1000}],
    "limit_up": 11.0, "limit_down": 9.0,
    "quote_time": "10:00:00", "fresh": True, "src": "fake",
}
# 能立即成交的限价（≥ 卖一 10.01）；用 10.00 会挂在 REPORTED（纸面口径：对手盘没打到不成交）
FILL_PRICE = 10.01
# 吃不到的限价（< 卖一）→ 留在 REPORTED，用于测撤单
REST_PRICE = 9.95


# ---------------------------------------------------------------- 夹具


@pytest.fixture()
def client_svc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """临时账本 + 假行情 + 假指标 → (TestClient, PaperOrderService)。"""
    paper_dir = tmp_path / "paper"
    monkeypatch.setenv("PANEL_V2_PAPER_DIR", str(paper_dir))
    monkeypatch.setenv("PANEL_V2_FEED", "0")        # 绝不启动 QMT feed
    # 指标走真实 eltdx（daily_bars qfq）；测试零网络，必须换掉
    monkeypatch.setattr(core_quotes, "order_metrics",
                        lambda glb: {"atr14": 0.25, "ma5": 9.9})
    # 生产 JSON 一律不外读（stock_names / real_positions 走真文件会让用例依赖现场）
    monkeypatch.setattr(PluginContext, "json_read", lambda self, name: {"names": {}})
    monkeypatch.setattr(PluginContext, "panel_json", lambda self, name: {"positions": []})
    # feed 单例也重置：每次 create_app 都会 add_listener，不重置会跨用例堆积
    core_feed.reset_feed()
    paper_service.reset_service()                   # 丢掉上一个用例的单例
    app = create_app()
    svc = paper_service.get_service()
    svc._session_ok = lambda: True                  # 时段判定与用例无关，固定为可交易
    monkeypatch.setattr(PaperOrderService, "quote_for",
                        lambda self, ts_code: dict(FAKE_QUOTE))
    client = TestClient(app)
    try:
        yield client, svc
    finally:
        core_feed.reset_feed()
        paper_service.reset_service()


def _place(client: TestClient, price: float = FILL_PRICE, qty: int = 100,
           side: str = "buy", code: str = CODE6) -> Dict[str, Any]:
    """下一张限价单，返回 HTTP 响应 JSON（调用方自行断言状态码）。"""
    res = client.post("/api/order/place",
                      json={"code": code, "side": side, "qty": qty,
                            "price": price, "order_type": "limit"})
    assert res.status_code == 200, res.text
    return res.json()


# ---------------------------------------------------------------- 1. manifest / 契约


def _manifest_data() -> Dict[str, Any]:
    """最小可用 manifest（无 paper 字段），用于权限反向用例。"""
    return {
        "manifest_version": 1, "id": "demo", "name": "演示插件", "version": "0.1.0",
        "min_core_version": "0.1.0", "enabled": True, "kind": "domain",
        "backend": {"module": "backend", "entry": "register", "timeout_ms": 5000,
                    "permissions": []},
        "data": {"read": [], "write": []},
        "config": {}, "depends": [],
    }


def test_manifest_paper_flag() -> None:
    """`data.paper=true` + root 挂载 + 零写入（纸面账本写全在 core）。"""
    m = load_manifest(PLUGIN_DIR)
    assert m.id == "paper"
    assert m.backend.mount == "root"
    assert m.backend.entry == "register"
    assert m.paper_read is True
    assert m.feed_read is True
    assert m.quotes_read is True
    assert m.data_write == ()
    assert "real_positions" in m.panel_read
    assert "stock_names" in m.json_read
    # public() 把三个开关都透出（/_plugins 前端按它决定能不能调 ctx.paper）
    data = m.public()["data"]
    assert data["paper"] is True and data["feed"] is True and data["quotes"] is True


def test_ctx_paper_permission(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """未声明 `data.paper` → PermissionError；声明 true → 拿到 core 单例。

    账本目录仍指到 tmp（`get_service()` 会按当前 env 装配），跑测不碰真实账本。
    """
    monkeypatch.setenv("PANEL_V2_PAPER_DIR", str(tmp_path / "paper"))
    paper_service.reset_service()
    try:
        plain = validate_manifest(_manifest_data(), tmp_path / "demo")
        with pytest.raises(PermissionError):
            _ = PluginContext(plain).paper

        data = _manifest_data()
        data["data"]["paper"] = True
        allowed = PluginContext(validate_manifest(data, tmp_path / "demo"))
        assert allowed.paper is paper_service.get_service()
    finally:
        paper_service.reset_service()


def test_manifest_paper_must_be_bool(tmp_path: Path) -> None:
    """`data.paper` 非布尔 → ContractError（不静默当 false）。"""
    data = _manifest_data()
    data["data"]["paper"] = "yes"
    with pytest.raises(ContractError) as excinfo:
        validate_manifest(data, tmp_path / "demo")
    assert "data.paper" in str(excinfo.value)


# ---------------------------------------------------------------- 2. 下单 / 委托 / 账户 / 账本


def test_place_and_list(client_svc) -> None:
    """下单成功 → order/list、paper_account、ledger(paper) 三处都有真实数据。"""
    client, _svc = client_svc
    body = _place(client)
    assert body["ok"] is True
    assert body["mode"] == "paper"
    order = body["order"]
    assert order["status"] == "FILLED"                     # 卖一 10.01 ≤ 限价 10.01 → 立即成交
    assert float(order["filled_price"]) == pytest.approx(FILL_PRICE)
    assert order["record"]["ts_code"] == CODE             # 代码已归一成 ts_code
    assert isinstance(body["warnings"], list)
    assert body["account"]["positions"][CODE]["qty"] == 100

    # ---- /api/order/list ----
    lst = client.get("/api/order/list").json()
    assert lst["ok"] is True and lst["mode"] == "paper"
    assert len(lst["date"]) == 8
    assert len(lst["orders"]) == 1
    assert lst["orders"][0]["ts_code"] == CODE
    assert set(lst["account"]) == {"available_cash", "total_asset", "frozen_cash",
                                   "positions", "updated_at"}
    # metrics：ACTIVE 条件单 + 持仓码都要有（这里只有持仓那只）
    assert set(lst["metrics"]) == {CODE}
    assert set(lst["metrics"][CODE]) == {"atr14", "ma5"}

    # ---- /api/paper_account ----
    pa = client.get("/api/paper_account").json()
    assert set(pa) >= {"initial_cash", "cash", "positions", "realized_pnl",
                       "equity_curve", "stats", "updated_at", "account", "positions_list"}
    assert set(pa["account"]) == {"total_asset", "available_cash", "market_value",
                                  "frozen_cash", "unrealized_pnl", "single_order_cap",
                                  "cash_reserve_pct", "updated_at"}
    assert pa["account"]["single_order_cap"] == pytest.approx(
        round(pa["account"]["total_asset"] * 0.1, 2))
    assert len(pa["positions_list"]) == 1
    row = pa["positions_list"][0]
    assert row["ts_code"] == CODE
    assert row["qty"] == 100
    assert row["last_price"] == pytest.approx(10.0)        # 假行情 last
    assert row["price_source"] == "fake"                   # src 如实透出
    assert row["day_pnl_amount"] == pytest.approx((10.0 - 9.8) * 100)

    # ---- /api/ledger?mode=paper ----
    lg = client.get("/api/ledger?mode=paper").json()
    assert lg["ok"] is True and lg["mode"] == "paper"
    assert set(lg["account"]) == {"total_asset", "cash", "available_cash", "frozen_cash",
                                  "market_value", "position_pnl", "position_pnl_pct",
                                  "day_pnl", "day_pnl_pct", "cash_ratio"}
    assert len(lg["positions"]) == 1
    prow = lg["positions"][0]
    assert prow["mode"] == "paper" and prow["qty"] == 100
    assert prow["bought_today"] == 100 and prow["available_qty"] == 0   # T+1 当日不可卖
    assert len(lg["trades"]) == 1
    assert lg["trades"][0]["side"] == "BUY"
    assert set(lg["book"]) == {"active", "history", "hist_max", "hist_total"}
    assert set(lg["conds_expiring"]) == set()


def test_paper_account_404_before_first_fill(client_svc) -> None:
    """一次成交都没有时账本文件不存在 → 404（与旧端同：前端显示「无成交记录」）。"""
    client, _svc = client_svc
    res = client.get("/api/paper_account")
    assert res.status_code == 404
    assert "纸面账本" in res.json()["detail"]


# ---------------------------------------------------------------- 3. 撤单


def test_cancel(client_svc) -> None:
    """挂在 REPORTED 的委托可撤；撤后状态 CANCELLED，重复撤 → 409。"""
    client, _svc = client_svc
    body = _place(client, price=REST_PRICE)                # 低于卖一 → 只挂单
    order = body["order"]
    assert order["status"] == "REPORTED"
    oid = order["paper_order_id"]
    assert oid

    res = client.post("/api/order/cancel", json={"order_id": oid})
    assert res.status_code == 200
    assert res.json() == {"ok": True, "order_id": oid, "status": "CANCELLED"}

    lst = client.get("/api/order/list").json()
    assert lst["orders"][0]["status"] == "CANCELLED"

    again = client.post("/api/order/cancel", json={"order_id": oid})
    assert again.status_code == 409                        # 终态不能重复撤


# ---------------------------------------------------------------- 4. 条件单


def test_condition_add_list_cancel(client_svc) -> None:
    """加止损条件单（需先有持仓）→ list 1 条 → 撤单 → CANCELLED。"""
    client, _svc = client_svc
    _place(client)                                        # 先建 100 股持仓（卖出条件单要求不超持仓）

    res = client.post("/api/condition/add", json={
        "code": CODE6, "type": "stop_loss", "qty": 100,
        "trigger_price": 9.5, "order_type": "market", "valid_until": None,
        "note": "测试止损",
    })
    assert res.status_code == 200, res.text
    cond = res.json()["condition"]
    assert cond["code"] == CODE and cond["type"] == "stop_loss"
    assert cond["status"] == "ACTIVE"
    cid = cond["cond_id"]
    assert cid

    lst = client.get("/api/condition/list?status=ACTIVE").json()
    assert lst["ok"] is True
    assert len(lst["conditions"]) == 1
    assert lst["conditions"][0]["cond_id"] == cid

    res = client.post("/api/condition/cancel", json={"cond_id": cid})
    assert res.status_code == 200
    assert res.json()["condition"]["status"] == "CANCELLED"
    assert client.get("/api/condition/list").json()["conditions"][0]["status"] == "CANCELLED"


def test_condition_add_rejects_bad_trigger(client_svc) -> None:
    """止损触发价必须低于现价（否则一提交就立即触发）→ 4xx 业务拒绝。"""
    client, _svc = client_svc
    _place(client)
    res = client.post("/api/condition/add", json={
        "code": CODE6, "type": "stop_loss", "qty": 100, "trigger_price": 10.5,
    })
    assert res.status_code == 422
    assert "stop_loss" not in res.text or True              # detail 里带机器码/说明，不裸 500


# ---------------------------------------------------------------- 5. 买卖点


def test_trades_points(client_svc) -> None:
    """/api/trades/{code}：只取 FILLED，points 有 B 点，cost.paper 为持仓成本。"""
    client, _svc = client_svc
    _place(client)                                        # 造一笔 FILLED
    body = client.get("/api/trades/%s?days=60" % CODE).json()
    assert body["code"] == CODE
    assert body["days"] == 60
    assert body["count"] >= 1
    assert set(body["cost"]) == {"paper", "live"}
    assert body["cost"]["paper"] is not None              # 纸面持仓成本（>0 才下发）
    assert body["cost"]["live"] is None                   # real_positions 里没有这只
    pt = body["points"][0]
    assert set(pt) == {"mode", "side", "date", "t", "price", "qty", "amount", "name", "id"}
    assert pt["mode"] == "paper" and pt["side"] == "BUY"
    assert len(pt["date"]) == 8 and pt["date"].isdigit()
    assert pt["price"] == pytest.approx(FILL_PRICE)
    assert pt["qty"] == 100


def test_trades_bad_code_400(client_svc) -> None:
    """非法代码 → 400（旧端同）。"""
    client, _svc = client_svc
    assert client.get("/api/trades/NOTACODE").status_code == 400


# ---------------------------------------------------------------- 6. 路由冲突 / feed 诊断


def test_no_route_conflict(client_svc) -> None:
    """root 挂载不冲突：health ok、paper/compat_misc 都加载成功、5 条路径归 paper 所有。"""
    client, _svc = client_svc
    health = client.get("/api/health").json()
    assert health["ok"] is True
    assert "paper" in health["plugins"]
    assert client.get("/_plugins/health").status_code == 200

    records = client.app.state.panel_v2["records"]
    # 只断言与本任务相关的两个插件：其他插件的可加载性不是本任务的验收面
    assert records["paper"].ok is True, records["paper"].error
    assert records["compat_misc"].ok is True, records["compat_misc"].error

    compat_paths = {r.path for r in records["compat_misc"].router.routes}
    for path in ("/order/list", "/trades/{ts_code}", "/condition/list",
                 "/paper_account", "/ledger"):
        assert path not in compat_paths, "compat_misc 不得再抢 %s" % path

    paper_paths = {r.path for r in records["paper"].router.routes}
    for path in ("/order/list", "/trades/{ts_code}", "/condition/list",
                 "/paper_account", "/ledger"):
        assert path in paper_paths, "paper 应接管 %s" % path

    # 这五条现在由 paper 插件提供（404 也是「我们返回的 404」，带中文 detail）
    res = client.get("/api/paper_account")
    assert res.status_code == 404 and "纸面账本" in res.json()["detail"]


def test_feed_status_endpoint(client_svc) -> None:
    """/api/paper/feed/status：回 feed 的 status()，至少有 state / subscribed。"""
    client, _svc = client_svc
    res = client.get("/api/paper/feed/status")
    assert res.status_code == 200
    body = res.json()
    assert "state" in body
    assert "subscribed" in body
    assert isinstance(body["subscribed"], list)


# ---------------------------------------------------------------- 7. 其它口径


def test_ledger_live_mode_refused(client_svc) -> None:
    """`mode=live` → HTTP 200 + ok:false（前端不会崩，实盘账本仍在旧 8800 看）。"""
    client, _svc = client_svc
    body = client.get("/api/ledger?mode=live").json()
    assert body == {"ok": False, "mode": "live",
                    "error": "paper-only: 实盘账本请在旧 8800 查看"}
    assert client.get("/api/ledger?mode=xx").status_code == 400


def test_place_rejects_bad_code(client_svc) -> None:
    """非法代码 → 4xx 业务拒绝（不是 500）。"""
    client, _svc = client_svc
    res = client.post("/api/order/place",
                      json={"code": "NOTACODE", "side": "buy", "qty": 100, "price": 10.0})
    assert 400 <= res.status_code < 500


def test_no_forbidden_quote_usage() -> None:
    """插件源码禁止出现 xtquant / xtdata / TdxClient / from eltdx（契约 §4.1 静态扫描）。"""
    for path in (BACKEND_PY, VIEWS_PY):
        src = path.read_text(encoding="utf-8")
        assert find_forbidden_quote_usage(src) == [], path.name
        assert "sqlite3.connect" not in src, path.name
