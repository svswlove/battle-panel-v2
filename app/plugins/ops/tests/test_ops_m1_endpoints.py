# -*- coding: utf-8 -*-
"""ops 插件 M1 补缺端点测试（A1~A4 + B1/B2 的 HTTP 壳）。

**铁律：绝不真触发**。触发类（amv_refresh / sector_backfill）全部在 core 层
monkeypatch 掉旧仓入口，测试只验：

1. `GET /panic` 的装配语义（库有无分档 / 快照何时取 / 分位与状态透传 / 120 日裁剪）；
2. `POST /amv_refresh` 的 200 ok:false / 429 busy / 504 timeout / 503 不可用 四档；
3. `POST /sector_track_backfill` 的 ok / started / busy / error 四态；
4. `POST /sector/restart` 已随 8002 下线摘除（2026-10-30）——验路由不再存在；
5. `GET/PUT /kline-indicator-settings` 的读、覆盖写、未知键保留、坏文件回 {}；
6. `GET /kline/{code}/xdxr` 的形状、非法代码 400、取数失败回空表；
7. 白名单外的名字/路径一律 403/404（双白名单校验真的在生效）。

panic 的算法不在这里测（那是旧仓口径，测它是第二套口径）；这里只验「接线对不对」。
"""
from __future__ import annotations

import copy
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import contract as core_contract  # noqa: E402
from app.core import ops_run as core_ops_run  # noqa: E402
from app.core import panic_calc  # noqa: E402
from app.core import paths as core_paths  # noqa: E402
from app.core.contract import PluginContext, validate_manifest  # noqa: E402
from app.plugins.ops import backend  # noqa: E402

PLUGIN_DIR = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- 夹具


class Env:
    """tmp 沙箱：白名单文件全指到临时目录，真 PluginContext（备份/原子/审计全跑）。"""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.root = tmp_path
        state_dir = tmp_path / "engine_state"
        panel_dir = tmp_path / "panel"
        misc_dir = tmp_path / "xiaoshi_cache"
        state_dir.mkdir(parents=True)
        panel_dir.mkdir(parents=True)
        misc_dir.mkdir(parents=True)
        self.state = {
            "ui_notes": state_dir / "ui_notes.json",
            "engine_state": state_dir / "engine_state.json",
        }
        self.panel = {
            "ops": panel_dir / "ops_status.json",
            "alert_plan_persistent": panel_dir / "alert_plan_persistent.json",
            "panic_breadth": panel_dir / "panic_breadth.json",
        }
        self.write_panel = {"alert_plan": self.panel["alert_plan_persistent"]}
        self.json_files = {"kline_indicator_settings": misc_dir / "kline_indicator_settings.json"}
        self.misc_files = {"kline_indicator_settings": misc_dir / "kline_indicator_settings.json"}

        for name, path in self.state.items():
            monkeypatch.setitem(core_paths.WRITABLE_STATE, name, path)
        for name, path in self.panel.items():
            monkeypatch.setitem(core_paths.READABLE_PANEL, name, path)
        for name, path in self.write_panel.items():
            monkeypatch.setitem(core_paths.WRITABLE_PANEL, name, path)
        for name, path in self.json_files.items():
            monkeypatch.setitem(core_paths.READABLE_JSON, name, path)
        for name, path in self.misc_files.items():
            monkeypatch.setitem(core_paths.WRITABLE_MISC, name, path)
        monkeypatch.setattr(core_paths, "OUT_DIR", tmp_path / "out")

        self.manifest = validate_manifest(
            json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8")), PLUGIN_DIR)
        self.ctx = PluginContext(self.manifest)
        self.app = FastAPI()
        self.app.include_router(backend.register(self.ctx), prefix="/api")
        self.client = TestClient(self.app)

    # -- 造数据 --
    def put_panel(self, name: str, obj: Any) -> Path:
        path = self.panel[name]
        path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        return path

    def put_json(self, name: str, obj: Any) -> Path:
        path = self.json_files[name]
        path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        return path

    def read_json(self, name: str) -> Any:
        return json.loads(self.json_files[name].read_text(encoding="utf-8"))

    def backups(self, name: str) -> List[Path]:
        bdir = core_paths.OUT_DIR / "backups" / name
        return sorted(bdir.glob("*.json")) if bdir.is_dir() else []


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    panic_calc.reset_cache()
    return Env(tmp_path, monkeypatch)


def _panic_doc(n: int = 260, last: str = "20261023") -> Dict[str, Any]:
    """造一份形状合法的序列库（只用它的结构，不用真生产数据）。"""
    series = []
    for i in range(n):
        day = "2026%04d" % (i + 1)
        series.append({
            "date": day, "up": 2000 + i, "down": 2000, "limit_down": 10,
            "up_pct": 50.0, "limit_down_pct": 0.5, "median_pct": 0.1,
            "q_limit_down": 40.0, "q_limit_down_count": 45.0,
            "q_up": 55.0, "q_median": 50.0,
            "panic": 50.0 + (i % 10), "state": "正常",
        })
    series[-1]["date"] = last
    return {"meta": {"count": n}, "series": series}


# ---------------------------------------------------------------- A1 GET /panic


def test_panic_no_store_503(env: Env) -> None:
    """序列库缺失 → 503 + no_store + hint（旧端同档）。"""
    res = env.client.get("/api/panic")
    assert res.status_code == 503
    body = res.json()
    assert body["ok"] is False and body["error"] == "no_store"
    assert "backfill" in body["hint"]


def test_panic_empty_series_503(env: Env) -> None:
    env.put_panel("panic_breadth", {"meta": {}, "series": []})
    res = env.client.get("/api/panic")
    assert res.status_code == 503 and res.json()["error"] == "no_store"


def _today8() -> str:
    """当天的 YYYYMMDD（实现是 `series[-1].date == 今天`，不是 `>=`，所以固定日期必过期）。"""
    return datetime.now().strftime("%Y%m%d")


def test_panic_store_mode_shape(env: Env) -> None:
    """库末行日期**等于今天**（当日已落库）→ 直接用库值，不取快照。"""
    today = _today8()
    env.put_panel("panic_breadth", _panic_doc(last=today))
    body = env.client.get("/api/panic").json()
    assert body["ok"] is True
    assert body["source"] == "store"
    assert body["mode"] in ("closed", "live", "close")
    assert body["window"] == 250
    assert [it["key"] for it in body["items"]] == ["limit_down", "up_ratio", "median", "amount"]
    assert set(body) == {"ok", "asof", "trade_date", "mode", "live", "source", "window",
                         "value", "state", "items", "series", "stage", "note",
                         "volume", "intraday_series"}, set(body)
    # volume：2026-10-29 新增的量能汇总（只加键）
    # intraday_series：2026-11-18 新增的**当日恐慌分时**（主人要「当日的恐慌走势图」）——
    #   tick_feed 每分钟落原始点，这里用日频 250 日历史现算分位/恐慌值；无记录时为 []（只加键）
    # 120 日裁剪（库里 260 行）
    assert len(body["series"]) == 120
    assert body["series"][-1]["date"] == today


def test_panic_snapshot_only_when_needed(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """库末行不是今天 且 处于 live/close 才取快照；其余时段一次都不取。

    这里锁的是「别把 eltdx 连接挂进 3s 渲染路径」这条省资源的铁律。
    """
    calls: List[str] = []

    def _fake_snapshot(_quotes: Any) -> Dict[str, Any]:
        calls.append("snap")
        return {}

    monkeypatch.setattr(panic_calc, "snapshot", _fake_snapshot)
    env.put_panel("panic_breadth", _panic_doc(last="20260101"))

    from datetime import datetime
    # closed 时段（开盘前）→ 不取快照
    body = panic_calc.payload(_FakeQuotes(), store_doc=_panic_doc(last="20260101"),
                             now_dt=datetime(2026, 10, 24, 8, 0, 0))
    assert calls == []
    assert body["ok"] is True and body["source"] == "store"

    # live 时段 → 取一次
    panic_calc.payload(_FakeQuotes(), store_doc=_panic_doc(last="20260101"),
                       now_dt=datetime(2026, 10, 23, 10, 30, 0))
    assert calls == ["snap"]


def test_panic_stage_attached(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """六阶段标签是旁注：取得到就带，取不到就是 null（不阻断主读数）。"""
    env.put_panel("panic_breadth", _panic_doc())
    monkeypatch.setattr(panic_calc, "stage", lambda: {"date": "20261023", "label": "高潮"})
    body = env.client.get("/api/panic").json()
    assert body["stage"] == {"date": "20261023", "label": "高潮"}

    monkeypatch.setattr(panic_calc, "stage", lambda: None)
    panic_calc.reset_cache()
    body2 = env.client.get("/api/panic").json()
    assert body2["stage"] is None
    assert body2["ok"] is True, "标签缺失不许把主读数打成失败"


def test_panic_snapshot_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """快照 TTL 2.8s：同一拍内多次取只打一次行情源。"""
    calls: List[int] = []

    def _fake_call(*_a: Any, **_k: Any) -> Any:
        calls.append(1)
        raise RuntimeError("down")   # 拿不到 → 空 dict，走降级分支

    class _Q:
        def call(self, *_a: Any, **_k: Any) -> Any:
            return _fake_call()

    monkeypatch.setattr(panic_calc, "_SNAP", {"at": 0.0, "data": None})
    first = panic_calc.snapshot(_Q())
    second = panic_calc.snapshot(_Q())
    assert first == {} and second == {}
    assert len(calls) == 1, "TTL 内第二次不该再打行情源"


def test_panic_session_kind() -> None:
    """读数归属：周末/非交易日 closed；09:30–15:00 live；15:00 之后 close。"""
    from datetime import datetime
    # 2026-10-24 是周六
    assert panic_calc.session_kind(datetime(2026, 10, 24, 10, 0))[0] == "closed"
    # 2026-10-23 是周五（交易日）
    assert panic_calc.session_kind(datetime(2026, 10, 23, 10, 0))[0] == "live"
    assert panic_calc.session_kind(datetime(2026, 10, 23, 15, 30))[0] == "close"
    assert panic_calc.session_kind(datetime(2026, 10, 23, 8, 0))[0] == "closed"
    assert panic_calc.session_kind(datetime(2026, 10, 23, 9, 30))[0] == "live"
    assert panic_calc.session_kind(datetime(2026, 10, 23, 15, 0))[0] == "live"


def test_panic_calc_uses_legacy_module() -> None:
    """算法唯一源：panic_calc 必须动态加载旧仓模块，且不复制分位公式。"""
    src = (ROOT / "app" / "core" / "panic_calc.py").read_text(encoding="utf-8")
    for sym in ("raw_from_snapshot", "score_row", "state_of", "load_store", "store_path",
                "turnover_context", "amount_ratio_window"):
        assert sym in src, "必须经旧仓函数，而不是自己算：%s" % sym
    # 禁止出现分位公式本体（拷一份就是第二口径）
    assert "less + 0.5 * equal" not in src
    assert "100.0 - (q_limit_down" not in src


# ---------------------------------------------------------------- A1 第四项·量能（2026-10-29）

_SNAP_DOWN = {"880005": {"last": 764, "open": 4466, "high": 5577, "pre_close": 2789},
              "880006": {"last": 39, "open": 42, "high": 48, "pre_close": 73},
              "880009": {"last": 765.89, "open": 783.18, "high": 783.95, "pre_close": 786.31}}
_SNAP_UP = {**_SNAP_DOWN, "880009": {"last": 800.0, "open": 783.18,
                                     "high": 803.0, "pre_close": 786.31}}


def _turnover_env(env: Env, monkeypatch: pytest.MonkeyPatch, mt: Dict[str, Any],
                  amounts: Dict[str, float]) -> None:
    """把量能因子的两个外部输入都换成隔离数据（不读真 .day、不读真 intraday.json）。"""
    path = env.root / "intraday.json"
    path.write_text(json.dumps({"market_turnover": mt}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("DSH_PANIC_INTRADAY", str(path))
    legacy = panic_calc._load_legacy()
    monkeypatch.setattr(legacy, "amount_by_day", lambda: amounts)
    monkeypatch.setattr(legacy, "amount_ratio_window", lambda cal, **kw: [1.0] * 40)


def test_panic_volume_item_forecast_on_down_day(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """盘中下跌日：第四项用**预估**全天额，并并列下发昨日同时刻比值。

    2026-11-19（夏天论证后）：量能**不参与打分**（active 恒 False），只在冰点区给确认标签。
    """
    _turnover_env(env, monkeypatch, {"asof": "2026-10-29 14:00:00", "progress": 0.9,
                                     "total_amount": 1.50e12,
                                     "forecast_full_day_amount": 1.60e12,
                                     "prev_same_time_amount": 1.45e12}, {"20261028": 1.70e12})
    monkeypatch.setattr(panic_calc, "snapshot", lambda _q: _SNAP_DOWN)
    from datetime import datetime
    body = panic_calc.payload(_FakeQuotes(), store_doc=_panic_doc(last="20260101"),
                              now_dt=datetime(2026, 10, 29, 14, 0))
    vol = body["items"][3]
    assert vol["key"] == "amount" and vol["active"] is False
    assert vol["kind"] == "forecast" and vol["state"] == "in"
    assert vol["value"] == round(1.60e12 / 1.70e12, 4)      # 预估全天 / 昨日全天
    assert vol["ratio_same_time"] == round(1.50e12 / 1.45e12, 4)   # 校验比：背离不改分
    assert vol["percentile"] is not None                    # 仍下发供排查
    # 确认标签：只有在冰点区（<25）才给；比值 <1 → 缩量未出清
    if body["value"] is not None and body["value"] < 25:
        assert vol["confirm"] == "缩量未出清"
    else:
        assert vol["confirm"] is None
    assert body["volume"]["kind"] == "forecast"


def test_panic_volume_skipped_on_up_day(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """上涨日：量能不计入（放量上涨不是恐慌），三项按有效项归一。"""
    _turnover_env(env, monkeypatch, {"asof": "2026-10-29 14:00:00", "progress": 0.9,
                                     "total_amount": 1.50e12,
                                     "forecast_full_day_amount": 1.60e12,
                                     "prev_same_time_amount": 1.45e12}, {"20261028": 1.70e12})
    monkeypatch.setattr(panic_calc, "snapshot", lambda _q: _SNAP_UP)
    from datetime import datetime
    body = panic_calc.payload(_FakeQuotes(), store_doc=_panic_doc(last="20260101"),
                              now_dt=datetime(2026, 10, 29, 14, 0))
    vol = body["items"][3]
    assert vol["active"] is False and vol["state"] == "up_day"
    assert vol["percentile"] is None
    legacy = panic_calc._load_legacy()
    assert body["value"] == legacy.panic_score(body["items"][0]["percentile"],
                                                body["items"][1]["percentile"],
                                                body["items"][2]["percentile"])


def test_panic_volume_close_uses_actual(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """收盘后：用实际全天额（不是预估），kind=actual。"""
    _turnover_env(env, monkeypatch, {"asof": "2026-10-29 15:30:00", "progress": 1.0,
                                     "total_amount": 1.55e12, "forecast_full_day_amount": 1.55e12,
                                     "prev_same_time_amount": 1.70e12}, {})
    monkeypatch.setattr(panic_calc, "snapshot", lambda _q: _SNAP_DOWN)
    from datetime import datetime
    body = panic_calc.payload(_FakeQuotes(), store_doc=_panic_doc(last="20260101"),
                              now_dt=datetime(2026, 10, 29, 15, 30))
    vol = body["items"][3]
    assert vol["kind"] == "actual"
    assert vol["amount"] == 1.55e12
    assert vol["value"] == round(1.55e12 / 1.70e12, 4)


class _FakeQuotes:
    """假行情门面（形状同 app/core/quotes.py）。"""

    def call(self, *_a: Any, **_k: Any) -> Any:
        return None

    def normalize(self, code: str) -> Optional[str]:
        from app.core.quotes import normalize
        return normalize(code)

    def xdxr(self, ts_code: str) -> List[Dict[str, Any]]:
        return []


# ---------------------------------------------------------------- A2 POST /amv_refresh


def _fake_action(monkeypatch: pytest.MonkeyPatch, result: Dict[str, Any]) -> List[str]:
    """把 core 的固定动作换成假实现（**绝不真跑** amv/backfill/restart）。"""
    calls: List[str] = []

    def _run_action(name: str) -> Dict[str, Any]:
        calls.append(name)
        return copy.deepcopy(result)

    monkeypatch.setattr(core_ops_run, "run_action", _run_action)
    return calls


def test_amv_refresh_ok(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_action(monkeypatch, {
        "ok": True, "asof": "2026-10-23 14:00:00",
        "all": {"amv": 1.4e13, "amv_pct": 1.2}, "coverage": {"pct": 0.995},
        "stale": False, "indices": 9,
    })
    res = env.client.post("/api/amv_refresh")
    assert res.status_code == 200
    assert calls == ["amv_refresh"]
    body = res.json()
    assert body["ok"] is True and body["indices"] == 9


def test_amv_refresh_no_new_data_is_200(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """无新数据（非交易/行情陈旧/闸门未过）→ 旧端是 **200 + ok:false + reason**，不是 4xx。"""
    _fake_action(monkeypatch, {"ok": False, "reason": "无新数据（非交易时段/行情陈旧/闸门未过）",
                               "asof": "2026-10-23 15:05:00", "all": None,
                               "coverage": None, "stale": False})
    res = env.client.post("/api/amv_refresh")
    assert res.status_code == 200
    assert res.json()["ok"] is False and "无新数据" in res.json()["reason"]


@pytest.mark.parametrize("code,status", [(429, 429), (504, 504), (503, 503), (500, 500)])
def test_amv_refresh_error_mapping(env: Env, monkeypatch: pytest.MonkeyPatch,
                                   code: int, status: int) -> None:
    """旧端 HTTPException 的分档原样保留：429 busy / 504 timeout / 503 不可用 / 500 失败。"""
    _fake_action(monkeypatch, {"ok": False, "status": "x", "http": code, "detail": "boom"})
    res = env.client.post("/api/amv_refresh")
    assert res.status_code == status
    assert res.json()["ok"] is False and res.json()["detail"] == "boom"


def test_amv_refresh_requires_manifest_flag(env: Env) -> None:
    """manifest 没声明 data.ops_run:true → PermissionError → 500（不碰底层）。"""
    raw = json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))
    raw["data"].pop("ops_run")
    ctx = PluginContext(validate_manifest(raw, PLUGIN_DIR))
    app = FastAPI()
    app.include_router(backend.register(ctx), prefix="/api")
    res = TestClient(app).post("/api/amv_refresh")
    assert res.status_code == 500
    assert "data.ops_run" in res.json()["detail"]


# ---------------------------------------------------------------- A3/A4 回填与拉起


def test_sector_backfill_started(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_action(monkeypatch, {"ok": True, "status": "started",
                                       "gaps": [["09:31", "09:35"]]})
    body = env.client.post("/api/sector_track_backfill").json()
    assert calls == ["sector_backfill"]
    assert body["status"] == "started" and body["gaps"]


def test_sector_backfill_ok_and_busy(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_action(monkeypatch, {"ok": True, "status": "ok", "gaps": [], "points": 240})
    body = env.client.post("/api/sector_track_backfill").json()
    assert body["status"] == "ok" and body["points"] == 240

    _fake_action(monkeypatch, {"ok": True, "status": "busy", "detail": "上一次补齐仍在进行"})
    body2 = env.client.post("/api/sector_track_backfill").json()
    assert body2["status"] == "busy", "busy 仍是 200（旧端同）"


def test_sector_backfill_error_is_200(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """工具不存在/检查失败 → 旧端是 `200 + {ok:false, status:error}`（前端按 status 分支）。"""
    _fake_action(monkeypatch, {"ok": False, "status": "error", "detail": "工具不存在"})
    res = env.client.post("/api/sector_track_backfill")
    assert res.status_code == 200
    assert res.json()["status"] == "error"


def test_sector_restart_route_removed(env: Env) -> None:
    """2026-10-30：8002 停机 + dashboard 目录销毁 → 该路由已不存在（404/405，不再有 410 桩）。"""
    res = env.client.post("/api/sector/restart")
    assert res.status_code in (404, 405), res.status_code


# ---------------------------------------------------------------- B1 指标设置


def test_kline_settings_get_missing_returns_empty(env: Env) -> None:
    """无文件 → `{}`（旧端同，不 404：前端 mergeSettings 当无设置处理）。"""
    res = env.client.get("/api/kline-indicator-settings")
    assert res.status_code == 200 and res.json() == {}


def test_kline_settings_get_corrupt_returns_empty(env: Env) -> None:
    path = env.json_files["kline_indicator_settings"]
    path.write_text("{not json", encoding="utf-8")
    assert env.client.get("/api/kline-indicator-settings").json() == {}


def test_kline_settings_roundtrip(env: Env) -> None:
    doc = {"main": {"ma": {"enabled": True}}, "kline_lc": {"tpl": "x"}}
    env.put_json("kline_indicator_settings", doc)
    assert env.client.get("/api/kline-indicator-settings").json() == doc

    # 前端是「GET 整分 → 只改 kline_lc → PUT 整分」，其余顶层键必须活着
    updated = dict(doc, kline_lc={"tpl": "y"})
    res = env.client.put("/api/kline-indicator-settings", json=updated)
    assert res.status_code == 200 and res.json() == {"ok": True}
    assert env.read_json("kline_indicator_settings") == updated
    assert env.backups("kline_indicator_settings"), "写前必须备份"


def test_kline_settings_put_empty_body(env: Env) -> None:
    """空 body → 落 `{}`（旧端 `json.dump(body or {})` 同口径）。"""
    res = env.client.put("/api/kline-indicator-settings", json=None)
    assert res.status_code == 200
    assert env.read_json("kline_indicator_settings") == {}


def test_kline_settings_write_goes_through_whitelist(env: Env) -> None:
    """manifest 没声明 data.misc → PermissionError → 500（白名单双校验真的在跑）。"""
    raw = json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))
    raw["data"].pop("misc")
    ctx = PluginContext(validate_manifest(raw, PLUGIN_DIR))
    app = FastAPI()
    app.include_router(backend.register(ctx), prefix="/api")
    res = TestClient(app).put("/api/kline-indicator-settings", json={"a": 1})
    assert res.status_code == 500
    assert "data.misc" in res.json()["detail"]


# ---------------------------------------------------------------- B2 除权除息


def test_xdxr_shape_and_passthrough(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    events = [
        {"date": "20260626", "category": 1, "type": "除权除息",
         "fenhong": 280.24, "songzhuangu": 0.0, "peigu": 0.0, "peigujia": 0.0},
        {"date": "20260627", "category": 2, "type": "送配股上市",
         "fenhong": None, "songzhuangu": None, "peigu": None, "peigujia": None},
    ]
    monkeypatch.setattr(env.ctx.__class__, "quotes",
                        property(lambda self: _XdxrQuotes(events)))
    body = env.client.get("/api/kline/600519.SH/xdxr").json()
    assert body["code"] == "600519"
    assert body["date"] == time.strftime("%Y%m%d"), "date 是查询日，不是事件日"
    assert body["events"] == events, "事件原样透传，不加工"


def test_xdxr_bad_code_matches_legacy_200(env: Env) -> None:
    """非法代码 → **200 + 空 events**（旧端 `api_kline_xdxr` 对任何字符串都回 200；
    2026-10-24 实测 8800 `/api/kline/notacode/xdxr` 就是 200）。"""
    res = env.client.get("/api/kline/notacode/xdxr")
    assert res.status_code == 200
    body = res.json()
    assert body["code"] == "notacode" and body["events"] == []
    assert body["date"] == time.strftime("%Y%m%d")


def test_xdxr_source_failure_returns_empty(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """行情源不可用 → 空 events（旧端 pytdx 全服失败也回空表，不编造）。"""
    def _boom(_code: str) -> List[Dict[str, Any]]:
        raise RuntimeError("eltdx down")

    monkeypatch.setattr(env.ctx.__class__, "quotes",
                        property(lambda self: _XdxrQuotes([], boom=True)))
    body = env.client.get("/api/kline/000001.SZ/xdxr").json()
    assert body["events"] == [] and body["code"] == "000001"


class _XdxrQuotes:
    def __init__(self, events: List[Dict[str, Any]], boom: bool = False) -> None:
        self._events = events
        self._boom = boom

    def normalize(self, code: str) -> Optional[str]:
        from app.core.quotes import normalize
        return normalize(code)

    def xdxr(self, ts_code: str) -> List[Dict[str, Any]]:
        if self._boom:
            raise RuntimeError("eltdx down")
        return self._events


# ---------------------------------------------------------------- 白名单外的名字


def test_panic_breadth_must_be_declared(env: Env) -> None:
    """双重白名单：manifest 少了 panic_breadth → 500（不是静默 404/空读数）。"""
    raw = json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))
    raw["data"]["panel"] = [p for p in raw["data"]["panel"] if p != "panic_breadth"]
    ctx = PluginContext(validate_manifest(raw, PLUGIN_DIR))
    app = FastAPI()
    app.include_router(backend.register(ctx), prefix="/api")
    res = TestClient(app).get("/api/panic")
    assert res.status_code == 500
    assert "panic_breadth" in res.json()["detail"]


def test_misc_write_rejects_undeclared_name(env: Env) -> None:
    """不在 WRITABLE_MISC 的名字 → PermissionError（不落盘）。"""
    with pytest.raises(PermissionError):
        env.ctx.misc_write("not_in_whitelist", {"a": 1})


def test_misc_write_requires_manifest_declaration(env: Env) -> None:
    raw = json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))
    raw["data"].pop("misc")
    ctx = PluginContext(validate_manifest(raw, PLUGIN_DIR))
    with pytest.raises(PermissionError):
        ctx.misc_write("kline_indicator_settings", {"a": 1})


def test_misc_write_is_atomic_and_audited(env: Env) -> None:
    """misc_write 与 panel_write 同规格：备份 + 原子 + 审计（旧端只有裸 tmp+replace）。"""
    path = env.misc_files["kline_indicator_settings"]
    path.write_text('{"old": 1}', encoding="utf-8")
    env.ctx.misc_write("kline_indicator_settings", {"new": 2})
    assert json.loads(path.read_text(encoding="utf-8")) == {"new": 2}
    assert env.backups("kline_indicator_settings"), "写前必须备份"
    audit = (core_paths.OUT_DIR / "backups" / "kline_indicator_settings" / "audit.log")
    assert audit.is_file() and "plugin=ops" in audit.read_text(encoding="utf-8")
    assert not (path.with_name(path.name + ".tmp")).exists(), "不留 .tmp 残骸"
