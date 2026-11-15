# -*- coding: utf-8 -*-
"""预警推送通道（app/core/notify.py）契约测试（2026-10-28）。

守的是 RESEARCH_20261028_cond_push.md §5 的三条硬约束：
  1. 配置：面板私有 notify.json 优先，缺失回退 Hana bridge.qq；status 不回传凭据；
  2. 队列：去重 300s、满了丢最旧并计数；enqueue 永不抛（行情线程调用）；
  3. 失败可见：发送失败写 health（consecutive_fails/last_error），单条失败不阻断后续。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

from app.core import notify  # noqa: E402


@pytest.fixture()
def tmp_notify(tmp_path, monkeypatch):
    monkeypatch.setattr(notify, "CONFIG_FILE", tmp_path / "notify.json")
    monkeypatch.setattr(notify, "HEALTH_FILE", tmp_path / "notify_health.json")
    monkeypatch.setattr(notify, "DEDUP_FILE", tmp_path / "notify_dedup.json")
    monkeypatch.setattr(notify, "LOG_FILE", tmp_path / "logs" / "notify.jsonl")
    monkeypatch.setattr(notify, "RECEIPT_FILE", tmp_path / "notify_receipts.json")
    monkeypatch.setattr(notify, "HANA_CONFIG", tmp_path / "absent.yaml")
    monkeypatch.setattr(notify, "HANA_CONFIG_FALLBACK", tmp_path / "absent_fallback.yaml")
    monkeypatch.setattr(notify, "_dedup", {})
    monkeypatch.setattr(notify, "_receipts", {})
    monkeypatch.setattr(notify, "_stats", {"sent": 0, "failed": 0, "dropped": 0})
    monkeypatch.setattr(notify, "_token", {"token": None, "expires": 0.0})
    # 不启动真实 sender 线程：否则线程会跨测试存活，用恢复后的真实 LOG_FILE/HEALTH_FILE 写盘
    monkeypatch.setattr(notify, "start", lambda: None)
    # 队列可能有前一条测试残留
    while not notify._q.empty():
        notify._q.get_nowait()
    return tmp_path


def _write_cfg(tmp_path: Path, enabled: bool = True) -> None:
    (tmp_path / "notify.json").write_text(json.dumps({
        "qq": {"enabled": enabled, "app_id": "102000000", "client_secret": "x" * 32, "openid": "OPENID"},
    }), encoding="utf-8")


def test_config_prefers_private_file(tmp_notify) -> None:
    _write_cfg(tmp_notify)
    cfg = notify.load_config()
    assert cfg["source"] == "notify.json" and cfg["enabled"] is True
    assert notify._configured(cfg) is True


def test_config_falls_back_to_hana(tmp_notify, monkeypatch) -> None:
    hana = tmp_notify / "hana.yaml"
    hana.write_text(
        "agent:\n  name: x\nbridge:\n  qq:\n    appID: '102000001'\n"
        "    appSecret: '" + "y" * 32 + "'\n    owner: 'OWNER'\n", encoding="utf-8")
    monkeypatch.setattr(notify, "HANA_CONFIG", hana)
    cfg = notify.load_config()
    assert cfg["source"].startswith("hana") and cfg["enabled"] is True
    assert cfg["app_id"] == "102000001"


def test_status_never_leaks_secret(tmp_notify) -> None:
    _write_cfg(tmp_notify)
    st = notify.status()
    blob = json.dumps(st, ensure_ascii=False)
    assert "x" * 32 not in blob and "client_secret" not in blob
    for key in ("channel", "enabled", "configured", "queued", "consecutive_fails"):
        assert key in st


def test_enqueue_dedup_and_disabled(tmp_notify, monkeypatch) -> None:
    monkeypatch.delenv("PANEL_V2_NOTIFY_DISABLE", raising=False)
    monkeypatch.setattr(notify, "send_qq", lambda *a, **k: None)
    # 未配置 → disabled，不抛
    assert notify.enqueue("COND_TRIGGERED", "t")["reason"] == "disabled"
    _write_cfg(tmp_notify)
    first = notify.enqueue("COND_TRIGGERED", "止损触发", "body", dedup_key="c1")
    second = notify.enqueue("COND_TRIGGERED", "止损触发", "body", dedup_key="c1")
    assert first["ok"] is True
    assert second == {"ok": False, "reason": "dedup"}


def test_queue_full_drops_oldest(tmp_notify, monkeypatch) -> None:
    monkeypatch.delenv("PANEL_V2_NOTIFY_DISABLE", raising=False)
    _write_cfg(tmp_notify)
    monkeypatch.setattr(notify, "send_qq", lambda *a, **k: None)
    monkeypatch.setattr(notify, "_q", notify.queue.Queue(maxsize=2))
    for i in range(4):
        notify.enqueue("ALERT", "t%d" % i, dedup_key="k%d" % i)
    assert notify._stats["dropped"] >= 1
    assert notify._q.qsize() <= 2


def test_send_failure_records_health(tmp_notify, monkeypatch) -> None:
    monkeypatch.delenv("PANEL_V2_NOTIFY_DISABLE", raising=False)
    _write_cfg(tmp_notify)
    monkeypatch.setattr(notify, "_http_json",
                        lambda *a, **k: (500, json.dumps({"message": "bad token"})))
    with pytest.raises(RuntimeError):
        notify.send_qq("标题", "正文")
    notify._record_health(False, "bad token")
    h = notify.read_health()
    assert h and h["consecutive_fails"] == 1 and "bad token" in (h["last_error"] or "")


def test_test_send_wraps_failure(tmp_notify, monkeypatch) -> None:
    monkeypatch.delenv("PANEL_V2_NOTIFY_DISABLE", raising=False)
    _write_cfg(tmp_notify)
    monkeypatch.setattr(notify, "_http_json", lambda *a, **k: (401, "denied"))
    res = notify.test_send()
    assert res["ok"] is False and "401" in res["detail"]


# ---------------------------------------------------------------- 2026-11-07 送达回执

def test_send_qq_parses_msg_id(tmp_notify, monkeypatch) -> None:
    """QQ 响应里的 id/timestamp 必须原样带回（2026-11-07 夏天要求“核得到送达”）。"""
    monkeypatch.delenv("PANEL_V2_NOTIFY_DISABLE", raising=False)
    _write_cfg(tmp_notify)
    body = json.dumps({"id": "MSG-ABC-123", "timestamp": "2026-11-07T17:10:00+08:00"})

    def fake(url, payload, headers):
        if "getAppAccessToken" in url:
            return 200, json.dumps({"access_token": "tok", "expires_in": 7200})
        return 200, body

    monkeypatch.setattr(notify, "_http_json", fake)
    res = notify.send_qq("标题", "正文")
    assert res["msg_id"] == "MSG-ABC-123"
    assert res["msg_ts"] == "2026-11-07T17:10:00+08:00"


def test_send_qq_without_id_is_not_confirmed(tmp_notify, monkeypatch) -> None:
    """HTTP 200 但无 id：只能算「已发出」，不能当送达。"""
    monkeypatch.delenv("PANEL_V2_NOTIFY_DISABLE", raising=False)
    _write_cfg(tmp_notify)

    def fake(url, payload, headers):
        if "getAppAccessToken" in url:
            return 200, json.dumps({"access_token": "tok", "expires_in": 7200})
        return 200, "{}"

    monkeypatch.setattr(notify, "_http_json", fake)
    res = notify.send_qq("标题", "正文")
    assert res["msg_id"] == ""
    item = {"ts": "2026-11-07 17:00:00", "event": "ALERT_HIT", "title": "t", "body": "b", "key": "k1"}
    notify._append_log(item, result=res)
    line = json.loads((tmp_notify / "logs" / "notify.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert line["status"] == "sent" and line["msg_id"] is None


def test_log_and_receipt_carry_msg_id(tmp_notify) -> None:
    item = {"ts": "2026-11-07 17:00:00", "event": "ALERT_HIT", "title": "t", "body": "b", "key": "k2"}
    res = {"msg_id": "MSG-XYZ", "msg_ts": "2026-11-07T17:00:01+08:00"}
    notify._append_log(item, result=res)
    notify._record_receipt("k2", {"status": "confirmed", "msg_id": "MSG-XYZ",
                                 "msg_ts": res["msg_ts"], "sent_at": "2026-11-07 17:00:01"})
    line = json.loads((tmp_notify / "logs" / "notify.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert line["status"] == "confirmed" and line["msg_id"] == "MSG-XYZ"
    rc = notify.receipt("k2")
    assert rc and rc["msg_id"] == "MSG-XYZ"
    # 回执落盘（进程重启可查）
    saved = json.loads((tmp_notify / "notify_receipts.json").read_text(encoding="utf-8"))
    assert saved["k2"]["status"] == "confirmed"


def test_health_records_msg_and_origin(tmp_notify) -> None:
    notify._record_health(True, info={"msg_id": "MSG-H1", "msg_ts": "2026-11-07T17:00:00+08:00"})
    h = notify.read_health()
    assert h["last_msg_id"] == "MSG-H1"
    assert h["origin"] in ("panel", "test")


def test_pytest_process_uses_isolated_sink() -> None:
    """进程级隔离：pytest 里 notify 模块的默认写盘目标不得指向生产目录。"""
    assert notify.IN_TEST is True
    assert "_tests" in str(notify.HEALTH_FILE)
    assert "_tests" in str(notify.LOG_FILE)
    assert "_tests" in str(notify.RECEIPT_FILE)


def _mini_app(tmp_path):
    from fastapi.testclient import TestClient
    from app.core.main import create_app
    web = tmp_path / "web"
    web.mkdir()
    (web / "shell.html").write_text("<!doctype html><title>t</title>", encoding="utf-8")
    (web / "shell.js").write_text("// stub", encoding="utf-8")
    (web / "shell.css").write_text("/* stub */", encoding="utf-8")
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    return TestClient(create_app(plugins_dir=plugins, web_dir=web))


def test_enqueue_wait_returns_receipt(tmp_path, monkeypatch) -> None:
    """内核带 wait 时，入队接口要把落地 msg_id 一并回去（2026-11-07）。"""
    monkeypatch.setattr(notify, "enqueue",
                        lambda *a, **k: {"ok": True, "queued": 1, "key": "k9"})
    monkeypatch.setattr(notify, "receipt",
                        lambda k: {"status": "confirmed", "msg_id": "M1",
                                   "msg_ts": "2026-11-07T17:00:00+08:00", "sent_at": "s"})
    with _mini_app(tmp_path) as client:
        res = client.post("/api/notify/enqueue", json={"title": "t", "wait": 0.5})
    body = res.json()
    assert body["status"] == "confirmed" and body["msg_id"] == "M1"


def test_enqueue_without_wait_is_queued(tmp_path, monkeypatch) -> None:
    """不带 wait / 等不到回执：只能报「已入队」，绝不冒充送达。"""
    monkeypatch.setattr(notify, "enqueue",
                        lambda *a, **k: {"ok": True, "queued": 2, "key": "k10"})
    monkeypatch.setattr(notify, "receipt", lambda k: None)
    with _mini_app(tmp_path) as client:
        res = client.post("/api/notify/enqueue", json={"title": "t"})
    body = res.json()
    assert body["status"] == "queued" and body.get("msg_id") is None


def test_receipt_endpoint_unknown_key(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(notify, "receipt", lambda k: None)
    with _mini_app(tmp_path) as client:
        res = client.get("/api/notify/receipt", params={"key": "nope"})
    body = res.json()
    assert body["ok"] is True and body["status"] == "unknown"
