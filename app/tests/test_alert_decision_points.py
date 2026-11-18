# -*- coding: utf-8 -*-
"""预警卡「决策点」分区 + 唤醒总开关 · 契约测试（2026-11-16）。

守四层：

1. **只读口径**：命中次数/触发值来自 `wake_events.jsonl` 原值，不重算条件；
   计划只原样展示（不出现「已满足/未满足」这类实时判定）。
2. **空态**：计划缺失 / 日期不符 / 无事件 / 心跳缺失，各自给明确状态，不拿旧文件充今天。
3. **开关语义**：关 = 不叫醒（wake_eval 侧由 `engine/tests/test_wake_eval.py` 守），
   面板侧必须能读到状态、能把状态写回、坏文件降级为「开 + 标异常」。
4. **写边界**：新写操作只有 `/alert/wake_switch` 一个，路径写死在 `common/wake_switch.py`；
   `decisions.py` 一个写动作都没有（静态扫）。

隔离：全部落 tmp，读写都不碰生产 `data/panel` / `engine/state`。
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]          # projects/panel-v2
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from app.plugins.alert import backend as B
    from app.plugins.alert import decisions as DU
    from app.core import paths as core_paths
    from app.core.contract import PluginContext, load_manifest
except ImportError as _exc:  # pragma: no cover - 独立部署（无 engine/）才走到
    pytest.skip("alert 插件依赖旧引擎模块（%s），本环境跳过" % _exc, allow_module_level=True)

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugins" / "alert"
TODAY8 = "20261116"
TODAY = "2026-11-16"


def _endpoint(path, method="POST", ctx=None):
    router = B.register(ctx)
    for r in router.routes:
        if getattr(r, "path", None) == path and method in getattr(r, "methods", set()):
            return r.endpoint
    raise AssertionError("路由不存在：%s %s" % (method, path))


def _plan_doc(trade_date=TODAY, plans=None, generated_at="2026-11-16 09:24:35"):
    return {
        "meta": {"trade_date": trade_date, "generated_at": generated_at,
                 "source": "build_wake_plan.py"},
        "plans": plans if plans is not None else [
            {"dp_id": "W1-EXEC-FAIL", "theme": "脚本执行失败", "severity": "alert",
             "condition": {"logic": "AND", "terms": [
                 {"metric": "exec_failure", "op": ">=", "value": 1}]},
             "confirm_points": 1, "window": "09:35-14:55",
             "hysteresis_exit": {"logic": "OR", "terms": [
                 {"metric": "exec_failure", "op": "<", "value": 1}]},
             "runbook": {"why": "应执行却失败", "questions": ["根因？"],
                         "data_to_check": ["执行留痕"], "options": ["补单", "上报"]}},
            {"dp_id": "W2-INDEX-3600", "theme": "大盘关键位失守", "severity": "action",
             "condition": {"logic": "AND", "terms": [
                 {"metric": "index_price", "code": "000001.SH", "op": "<", "value": 3600.0}]},
             "confirm_points": 2, "require_cross_down": True, "window": "09:35-14:55",
             "hysteresis_exit": {"logic": "OR", "terms": [
                 {"metric": "index_price", "code": "000001.SH", "op": ">", "value": 3610.8}]},
             "runbook": {"why": "跌破 3600", "questions": ["破位还是插针？"],
                         "data_to_check": ["指数分时"], "options": ["维持", "减仓建议"]}},
        ],
    }


def _ev(ts="2026-11-16 09:40:08", trade_date=TODAY, dp_id="W2-INDEX-3600", value=3598.38):
    return {"ts": ts, "trade_date": trade_date, "dp_id": dp_id, "theme": "大盘关键位失守",
            "severity": "action", "confirm_points": 2, "window": "09:35-14:55",
            "condition": {"logic": "AND", "terms": [
                {"metric": "index_price", "code": "000001.SH", "op": "<", "value": 3600.0}]},
            "trigger_values": [{"metric": "index_price", "op": "<", "threshold": 3600.0,
                                "value": value, "code": "000001.SH"}],
            "runbook": {}}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    panel = tmp_path / "panel"
    state = tmp_path / "state"
    panel.mkdir()
    state.mkdir()
    monkeypatch.setattr(DU, "PANEL_DIR", str(panel))
    monkeypatch.setattr(DU, "STATE_DIR", str(state))
    monkeypatch.setattr(DU, "EVENTS_FILE", str(state / "wake_events.jsonl"))
    monkeypatch.setattr(DU, "STATUS_FILE", str(state / "wake_eval_status.json"))
    # 开关状态文件走**受控写白名单**（ctx.state_write），审计 jsonl 走模块；两者都指到 tmp
    switch_file = state / "wake_switch.json"
    monkeypatch.setitem(core_paths.WRITABLE_STATE, "wake_switch", switch_file)
    monkeypatch.setattr(core_paths, "OUT_DIR", tmp_path / "out")
    monkeypatch.setattr(B.WAKE_SW, "SWITCH_FILE", str(switch_file))
    monkeypatch.setattr(B.WAKE_SW, "AUDIT_FILE", str(state / "wake_switch_audit.jsonl"))
    return {"panel": panel, "state": state, "switch": switch_file, "out": tmp_path / "out"}


@pytest.fixture()
def ctx():
    """真 PluginContext（真双白名单校验），不是桩。"""
    return PluginContext(load_manifest(PLUGIN_DIR))


def _write_plan(env, doc=None):
    (env["panel"] / ("wake_plan_%s.json" % TODAY8)).write_text(
        json.dumps(doc if doc is not None else _plan_doc(), ensure_ascii=False), encoding="utf-8")


def _write_events(env, rows):
    (env["state"] / "wake_events.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def _write_status(env, doc):
    (env["state"] / "wake_eval_status.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------- 形态与只读

def test_routes_exist():
    paths = {getattr(r, "path", None) for r in B.register(None).routes}
    assert "/alert/decision_points" in paths
    assert "/alert/wake_switch" in paths


def test_decisions_module_has_no_write_code():
    """分区是纯只读：一个写动作都不许有（开关写在 common/wake_switch.py）。"""
    src = (PLUGIN_DIR / "decisions.py").read_text(encoding="utf-8")
    for token in ("json.dump(", ".write_text(", ".write_bytes(", "os.replace(", "os.remove(",
                  ".mkdir(", ".unlink(", ".touch("):
        assert token not in src, "decisions.py 出现写操作：%s" % token
    assert not re.search(r"open\s*\([^)]*[\"'](?:w|a|wb|ab|x)[\"']", src), \
        "decisions.py 出现 open 写模式"


def test_frontend_has_decision_block_markers():
    src = (PLUGIN_DIR / "frontend.js").read_text(encoding="utf-8")
    assert "/alert/decision_points" in src
    assert "/alert/wake_switch" in src
    assert "唤醒总开关已关闭" in src, "关闭态必须有醒目横幅文案"
    assert "不标已满足/未满足" in src


# ---------------------------------------------------------------- 数据装配

def test_plan_present_and_items_rendered(env):
    _write_plan(env)
    d = DU.build(date8=TODAY8)
    assert d["plan"]["present"] is True
    assert d["plan"]["count"] == 2
    ids = [x["dp_id"] for x in d["plan"]["items"]]
    assert ids == ["W1-EXEC-FAIL", "W2-INDEX-3600"]
    w2 = d["plan"]["items"][1]
    assert w2["condition_text"] == "指数价(000001.SH) < 3600"
    assert w2["require_cross_down"] is True
    assert w2["runbook"]["why"] == "跌破 3600"
    # 计划条目里不许出现实时判定字样
    blob = json.dumps(d["plan"], ensure_ascii=False)
    assert "已满足" not in blob and "未满足" not in blob


def test_empty_plan_is_explicit(env):
    d = DU.build(date8=TODAY8)
    assert d["plan"]["present"] is False
    assert d["plan"]["reason"] == "not_generated"
    assert d["plan"]["items"] == []


def test_plan_trade_date_mismatch_rejected(env):
    """文件名是今天、里面不是今天 → 拒收，不把昨天计划当今天展示。"""
    _write_plan(env, _plan_doc(trade_date="2026-11-13"))
    d = DU.build(date8=TODAY8)
    assert d["plan"]["present"] is False
    assert d["plan"]["reason"] == "trade_date_mismatch"
    assert d["plan"]["trade_date"] == "2026-11-13"


def test_latest_plan_hint_only_when_missing(env):
    """今天缺计划 → 给出最近一份仅作提示；今天有计划时不提示。"""
    (env["panel"] / "wake_plan_20261113.json").write_text(
        json.dumps(_plan_doc(trade_date="2026-11-13"), ensure_ascii=False), encoding="utf-8")
    d = DU.build(date8=TODAY8)
    assert d["plan"]["latest_plan_date"] == "2026-11-13"
    _write_plan(env)
    d = DU.build(date8=TODAY8)
    assert d["plan"]["latest_plan_date"] is None


def test_hits_are_raw_values_and_counted_per_dp(env):
    _write_plan(env)
    _write_events(env, [
        _ev(ts="2026-11-16 09:40:08", value=3598.38),
        _ev(ts="2026-11-16 13:57:14", value=3597.2),
    ])
    d = DU.build(date8=TODAY8)
    assert d["hits"]["count"] == 2
    w2 = [x for x in d["plan"]["items"] if x["dp_id"] == "W2-INDEX-3600"][0]
    assert w2["hits"] == 2
    assert w2["last_hit_ts"] == "2026-11-16 13:57:14"
    # 触发值原样：3579.2 不会被四舍五入成 3600 之类
    assert w2["last_trigger_values"][0]["value"] == 3597.2
    assert w2["last_trigger_values"][0]["threshold"] == 3600.0
    assert "3597.2" in w2["last_trigger_text"]
    timeline = d["hits"]["timeline"]
    assert [t["ts"] for t in timeline] == ["2026-11-16 09:40:08", "2026-11-16 13:57:14"]


def test_events_from_other_day_are_excluded(env):
    _write_plan(env)
    _write_events(env, [
        _ev(ts="2026-11-13 10:00:00", trade_date="2026-11-13"),
        _ev(ts="2026-11-16 09:40:08"),
    ])
    d = DU.build(date8=TODAY8)
    assert d["hits"]["count"] == 1, "昨天的命中不能算今天"
    assert d["hits"]["timeline"][0]["ts"] == "2026-11-16 09:40:08"


def test_no_events_is_normal_state(env):
    _write_plan(env)
    d = DU.build(date8=TODAY8)
    assert d["hits"]["count"] == 0
    assert d["hits"]["timeline"] == []
    assert d["plan"]["present"] is True


def test_broken_event_line_skipped(env):
    _write_plan(env)
    (env["state"] / "wake_events.jsonl").write_text(
        json.dumps(_ev(ts="2026-11-16 09:40:08"), ensure_ascii=False) + "\n{half line\n",
        encoding="utf-8")
    d = DU.build(date8=TODAY8)
    assert d["hits"]["count"] == 1
    assert d["hits"]["bad_lines"] == 1


def test_unplanned_hit_listed_not_hidden(env):
    _write_plan(env)
    _write_events(env, [_ev(dp_id="W9-OTHER")])
    d = DU.build(date8=TODAY8)
    assert d["hits"]["count"] == 1
    assert [u["dp_id"] for u in d["hits"]["unplanned"]] == ["W9-OTHER"]


def test_evaluator_states(env):
    _write_plan(env)
    d = DU.build(date8=TODAY8)
    assert d["evaluator"]["state"] == "UNKNOWN", "没有心跳文件 → 状态未知"

    _write_status(env, {"status": "EXITED", "pid": 1, "last_heartbeat": "2026-11-16 15:30:16",
                        "plan_path": "C:/x/wake_plan_20261116.json"})
    d = DU.build(date8=TODAY8)
    assert d["evaluator"]["state"] == "EXITED"
    assert d["evaluator"]["plan_path_matches_today"] is True

    _write_status(env, {"status": "RUNNING", "pid": 2, "last_heartbeat": "2026-11-16 10:00:00"})
    d = DU.build(date8=TODAY8, now=__import__("datetime").datetime(2026, 11, 16, 10, 1, 0))
    assert d["evaluator"]["state"] == "RUNNING"
    assert d["evaluator"]["stale"] is False
    assert d["evaluator"]["heartbeat_age_s"] == 60

    d = DU.build(date8=TODAY8, now=__import__("datetime").datetime(2026, 11, 16, 11, 0, 0))
    assert d["evaluator"]["stale"] is True, "60s/轮，3 轮没跳就标陈旧"


# ---------------------------------------------------------------- 开关（唯一写操作）

def test_switch_default_on_when_file_missing(env):
    d = DU.build(date8=TODAY8)
    assert d["switch"]["enabled"] is True
    assert d["switch"]["ok"] is True
    assert d["switch"]["reason"] == "missing_default_on"


def test_switch_write_then_read_back(env, ctx):
    post = _endpoint("/alert/wake_switch", ctx=ctx)
    r = post({"enabled": False})
    assert r["ok"] is True
    assert r["switch"]["enabled"] is False
    assert r["switch"]["ok"] is True
    d = DU.build(date8=TODAY8)
    assert d["switch"]["enabled"] is False
    assert d["switch"]["by"] == "panel"
    # 审计：追加一行，含来源
    audit = (env["state"] / "wake_switch_audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(audit) == 1
    rec = json.loads(audit[0])
    assert rec["enabled"] is False and rec["by"] == "panel"
    assert rec["source"] == "alert_panel"
    assert rec["prev_enabled"] is True

    r = _endpoint("/alert/wake_switch", ctx=ctx)({"enabled": True})
    assert r["ok"] is True and r["switch"]["enabled"] is True
    audit2 = (env["state"] / "wake_switch_audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(audit2) == 2, "审计是追加不是覆盖"


def test_switch_write_goes_through_controlled_channel(env, ctx):
    """写必须落 core 的受控通道：文件到位 + 写前备份 + 核心审计日志。"""
    _endpoint("/alert/wake_switch", ctx=ctx)({"enabled": False})
    assert env["switch"].is_file()
    assert json.loads(env["switch"].read_text(encoding="utf-8"))["enabled"] is False

    bdir = env["out"] / "backups" / "wake_switch"
    # 首次写时原文件不存在 → 无备份；再写一次必有备份（旧值 True）
    _endpoint("/alert/wake_switch", ctx=ctx)({"enabled": True})
    backups = sorted(bdir.glob("*.json"))
    assert backups, "第二次写必须备份上一版"
    assert json.loads(backups[-1].read_text(encoding="utf-8"))["enabled"] is False
    audit = bdir / "audit.log"
    assert audit.is_file(), "核心审计必须留痕"
    last = audit.read_text(encoding="utf-8").strip().splitlines()[-1]
    assert "plugin=alert" in last and "file=wake_switch" in last
    assert not env["switch"].with_name("wake_switch.json.tmp").exists()


def test_switch_write_refused_without_manifest_declaration(env):
    """未在 manifest.data.state 声明 → 拒绝写（不能靠模块自带的锁/原子绕过通道）。"""
    import dataclasses

    m = load_manifest(PLUGIN_DIR)
    naked = dataclasses.replace(m, state_write=())
    r = _endpoint("/alert/wake_switch", ctx=PluginContext(naked))({"enabled": False})
    assert r["ok"] is False
    assert "未登记可写" in r["error"]
    assert not env["switch"].exists(), "拒绝时不许落盘"


def test_switch_write_refused_without_ctx(env):
    r = _endpoint("/alert/wake_switch")({"enabled": False})
    assert r["ok"] is False
    assert not env["switch"].exists()


def test_switch_rejects_bad_payload(env, ctx):
    post = _endpoint("/alert/wake_switch", ctx=ctx)
    assert post({})["ok"] is False
    for bad in ("0", 0, None, [], {}):
        r = post({"enabled": bad})
        assert r["ok"] is False, bad
    assert not env["switch"].exists(), "非法入参不许落盘"


def test_switch_corrupt_file_degrades_to_on(env):
    (env["state"] / "wake_switch.json").write_text("{broken", encoding="utf-8")
    d = DU.build(date8=TODAY8)
    assert d["switch"]["enabled"] is True, "读不出按开（误唤醒比漏唤醒安全）"
    assert d["switch"]["ok"] is False, "但必须标异常，面板据此挂警示"
    assert d["switch"]["reason"] == "unreadable_default_on"


def test_switch_invalid_field_degrades_to_on(env):
    (env["state"] / "wake_switch.json").write_text('{"enabled": "0"}', encoding="utf-8")
    d = DU.build(date8=TODAY8)
    assert d["switch"]["enabled"] is True
    assert d["switch"]["ok"] is False


# ---------------------------------------------------------------- 参数校验

def test_bad_date_param_rejected(env):
    r = _endpoint("/alert/decision_points", "GET")(date="2026-13-99")
    assert r["ok"] is False and r["error"] == "bad_date"
