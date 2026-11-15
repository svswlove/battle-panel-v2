"""预警推送通道（2026-10-28，主人标注 48 / 调研 RESEARCH_20261028_cond_push.md 方案 B）。

只做一件事：把「要推的消息」异步、去重、可观测地发到 QQ 官方机器人。
- 通道：QQ 官方 bot C2C（移植 engine/common/qq_push.py 的发送体，stdlib-only）。
- 异步：feed 派发线程 / 条件单评估**绝不能**同步发 HTTP（10s 超时 = 全市场行情停摆）；
  这里入队，独立 sender 线程发，队列满丢最旧并计数。
- 去重：dedup_key（默认 event:title）300s 窗口，防 feed 50ms 重放 / OCO 双触发刷屏。
- 可观测：health 落盘（last_ok_ts / consecutive_fails）、JSONL 日志、/api/notify/status。
- **送达可核（2026-11-07 夏天复核要求）**：QQ 发送响应里的落地消息 id + 时间戳必须落盘。
  只有 `id` 才算「已确认送达」；HTTP 200 但无 id 只能算「已发出」；入队只能算「已入队」。
  每次投递按 dedup_key 记一张回执（`notify_receipts.json`），供内核/页面查询。
- **健康只由生产进程写**：pytest 进程一律重定向到 `OUT_DIR/_tests/` 下的隔离目录，
  不碰生产 `notify_health.json` / `notify.jsonl`；health 里带 `origin` 标来源。
- 凭证：面板私有 data/panel_v2/notify.json 优先；缺失时回退 Hana 宿主 bridge.qq（只读）。
  凭据内容永不回传（status 只回「已配置」布尔）。

边界：本模块不评估条件单、不写账本；评估在 cond_watch（独立进程）或 paper 服务里，
命中后调 enqueue()。契约见 docs/RESEARCH_20261028_cond_push.md §5。
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from app.core import paths

logger = logging.getLogger("panel_v2.notify")

# 测试进程硬隔离（2026-11-07）：pytest 里 notify 模块的任何写盘都落到 `_tests/` 子目录。
# 为什么在 import 期判定：sender 线程会跨测试存活，运行期再判会漏；进程级常量才可靠。
IN_TEST: bool = "pytest" in sys.modules or os.environ.get("PANEL_V2_NOTIFY_ORIGIN") == "test"
ORIGIN: str = "test" if IN_TEST else "panel"
_OUT_BASE: Path = (paths.OUT_DIR / "_tests") if IN_TEST else paths.OUT_DIR

CONFIG_FILE: Path = _OUT_BASE / "notify.json"
HEALTH_FILE: Path = _OUT_BASE / "notify_health.json"
DEDUP_FILE: Path = _OUT_BASE / "notify_dedup.json"
LOG_FILE: Path = _OUT_BASE / "logs" / "notify.jsonl"
RECEIPT_FILE: Path = _OUT_BASE / "notify_receipts.json"
RECEIPT_KEEP = 200          # 回执表上限（超出保留最近 200 条，够一天查询）

TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
API_BASE = "https://api.sgroup.qq.com"
HTTP_TIMEOUT = 10.0
TOKEN_TTL = 7200
TOKEN_EARLY = 300
DEDUP_WINDOW = 300.0
QUEUE_MAX = 200
LOG_MAX_BYTES = 2 * 1024 * 1024


# 模块加载时补读一次回执表（进程重启后仍能回应已投递的查询）。
# 放在函数定义之后调用（见文件末尾 _load_receipts()）。

# Hana 宿主凭证（只在面板私有配置缺失时读）。
# 2026-10-28 修正：旧引擎 qq_push 读的是 agent-mt9jqvxf 的凭证，该应用已被 QQ 停用
# （token 接口 100016 invalid appid or secret）；当前活跃通道是 zero-one 那套。
# 默认读 PANEL_V2_QQ_AGENT（默认 zero-one），失败再回退 agent-mt9jqvxf（兼容旧部署）。
HANA_HOME: Path = Path(os.environ.get("HANA_HOME", Path.home() / ".hanako"))
HANA_AGENT: str = os.environ.get("PANEL_V2_QQ_AGENT", "zero-one")
HANA_CONFIG: Path = HANA_HOME / "agents" / HANA_AGENT / "config.yaml"
HANA_CONFIG_FALLBACK: Path = HANA_HOME / "agents" / "agent-mt9jqvxf" / "config.yaml"

_q: "queue.Queue[dict[str, Any]]" = queue.Queue(maxsize=QUEUE_MAX)
_sender: threading.Thread | None = None
_sender_lock = threading.Lock()
_dedup: dict[str, float] = {}
_dedup_lock = threading.Lock()
_token = {"token": None, "expires": 0.0}
_stats = {"sent": 0, "failed": 0, "dropped": 0}
#: 投递回执：dedup_key → {status, msg_id, msg_ts, sent_at, error}
_receipts: dict[str, dict[str, Any]] = {}
_receipt_lock = threading.Lock()


# ---------------------------------------------------------------- 配置

def _read_yaml_qq(path: Path) -> dict[str, Any]:
    """从 Hana config.yaml 的 bridge.qq 段取 appID/appSecret/owner（无 pyyaml 依赖）。"""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    out: dict[str, Any] = {}
    qq_indent = None
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" \t"))
        if qq_indent is None:
            if stripped == "qq:":
                qq_indent = indent
            continue
        if indent <= qq_indent:
            break
        m = re.match(r"^([A-Za-z_]\w*):\s*(.*?)\s*$", stripped)
        if m and m.group(1) in ("appID", "appSecret", "owner"):
            val = m.group(2).strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ("\"", "'"):
                val = val[1:-1]
            out[m.group(1)] = val.split(" #")[0].strip()
    return out


def load_config() -> dict[str, Any]:
    """读面板私有 notify.json；缺失/不完整时回退 Hana 宿主 bridge.qq。

    返回 {enabled, app_id, client_secret, openid, source}；凭据字段只在进程内使用。
    """
    cfg: dict[str, Any] = {"enabled": False, "app_id": "", "client_secret": "", "openid": "", "source": "none"}
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        qq = raw.get("qq") if isinstance(raw, dict) else None
        if isinstance(qq, dict):
            cfg["enabled"] = bool(qq.get("enabled"))
            cfg["app_id"] = str(qq.get("app_id") or "")
            cfg["client_secret"] = str(qq.get("client_secret") or "")
            cfg["openid"] = str(qq.get("openid") or "")
            cfg["source"] = "notify.json"
    except (OSError, ValueError):
        pass
    if not (cfg["app_id"] and cfg["client_secret"] and cfg["openid"]):
        for path in (HANA_CONFIG, HANA_CONFIG_FALLBACK):
            host = _read_yaml_qq(path)
            if host.get("appID") and host.get("appSecret") and host.get("owner"):
                cfg["app_id"] = host["appID"]
                cfg["client_secret"] = host["appSecret"]
                cfg["openid"] = host["owner"]
                cfg["source"] = "hana:" + path.parent.name
                cfg["enabled"] = True
                break
    return cfg


def _configured(cfg: dict[str, Any] | None = None) -> bool:
    c = cfg or load_config()
    return bool(c.get("app_id") and c.get("client_secret") and c.get("openid"))


def _env_disabled() -> bool:
    """测试/演练硬开关：PANEL_V2_NOTIFY_DISABLE=1 时绝不真发（默认由 app/tests/conftest.py 打开）。"""
    return os.environ.get("PANEL_V2_NOTIFY_DISABLE", "0") == "1"


# ---------------------------------------------------------------- 发送

def _http_json(url: str, payload: dict[str, Any] | None, headers: dict[str, str]) -> tuple[int, str]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if payload is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:  # noqa: PERF203 - 4xx/5xx 也要读 body
        return exc.code, exc.read().decode("utf-8", "replace")


def _access_token(cfg: dict[str, Any]) -> str:
    now = time.time()
    if _token["token"] and now < _token["expires"] - TOKEN_EARLY:
        return _token["token"]
    status, body = _http_json(TOKEN_URL,
                              {"appId": cfg["app_id"], "clientSecret": cfg["client_secret"]},
                              {"Content-Type": "application/json"})
    if status != 200:
        raise RuntimeError("QQ token 获取失败 [%s]: %s" % (status, body[:160]))
    data = json.loads(body)
    token = data.get("access_token")
    if not token:
        raise RuntimeError("QQ token 响应缺 access_token")
    _token["token"] = token
    _token["expires"] = now + int(data.get("expires_in") or TOKEN_TTL)
    return token


def send_qq(title: str, body: str) -> dict[str, Any]:
    """同步发送一条（仅 sender 线程 / /api/notify/test 使用）。失败抛 RuntimeError。

    成功返回 `{msg_id, msg_ts}`：QQ 官方接口回 `{id, timestamp}`，**id 就是落地消息 id**。
    2026-11-07 起不再只看 HTTP 200：没有 id 只算「已发出」，有 id 才算「已确认」。
    """
    if _env_disabled():
        raise RuntimeError("notify disabled by PANEL_V2_NOTIFY_DISABLE")
    cfg = load_config()
    if not _configured(cfg):
        raise RuntimeError("QQ 通道未配置（notify.json 或 Hana bridge.qq 均缺）")
    md = ("**%s**\n\n%s" % (title.strip(), body.strip())).strip()
    token = _access_token(cfg)
    status, resp = _http_json(
        "%s/v2/users/%s/messages" % (API_BASE, cfg["openid"]),
        {"content": " ", "msg_type": 2, "markdown": {"content": md}},
        {"Authorization": "QQBot %s" % token, "Content-Type": "application/json"},
    )
    if status != 200:
        raise RuntimeError("QQ 发送失败 [%s]: %s" % (status, resp[:160]))
    try:
        data = json.loads(resp) if resp.strip() else {}
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {"msg_id": str(data.get("id") or ""),
            "msg_ts": str(data.get("timestamp") or "")}


# ---------------------------------------------------------------- 健康 / 日志

def _record_health(ok: bool, err: Any = None, info: dict[str, Any] | None = None) -> None:
    """写健康快照。`origin` 标明写者（panel=生产进程 / test=测试隔离目录）。"""
    try:
        try:
            h = json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
            if not isinstance(h, dict):
                h = {}
        except (OSError, ValueError):
            h = {}
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        h["updated_at"] = now
        h["origin"] = ORIGIN
        if ok:
            h["last_ok_ts"] = now
            h["consecutive_fails"] = 0
            h["last_error"] = None
            if info and info.get("msg_id"):
                h["last_msg_id"] = info["msg_id"]
                h["last_msg_ts"] = info.get("msg_ts") or now
        else:
            h["last_fail_ts"] = now
            h["consecutive_fails"] = int(h.get("consecutive_fails") or 0) + 1
            h["last_error"] = str(err)[:200]
        health = dict(h)
        health["sent"] = _stats["sent"]
        health["failed"] = _stats["failed"]
        health["dropped"] = _stats["dropped"]
        HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = HEALTH_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(health, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, HEALTH_FILE)
    except Exception:  # noqa: BLE001 - 健康落盘失败不得影响主流程
        pass


def _append_log(item: dict[str, Any], error: str | None = None,
                result: dict[str, Any] | None = None) -> None:
    """写一条推送日志。真实推送必须带 `status` + `msg_id`（有 id 才算落地）。"""
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            if LOG_FILE.stat().st_size > LOG_MAX_BYTES:
                LOG_FILE.write_text("", encoding="utf-8")
        except OSError:
            pass
        rec = dict(item)
        rec["origin"] = ORIGIN
        if error:
            rec["status"] = "failed"
            rec["error"] = error[:200]
        else:
            res = result or {}
            msg_id = str(res.get("msg_id") or "")
            rec["status"] = "confirmed" if msg_id else "sent"
            rec["msg_id"] = msg_id or None
            rec["msg_ts"] = res.get("msg_ts") or None
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _save_receipts() -> None:
    try:
        RECEIPT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _receipt_lock:
            items = list(_receipts.items())[-RECEIPT_KEEP:]
            doc = dict(items)
        tmp = RECEIPT_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, RECEIPT_FILE)
    except OSError:
        pass


def _load_receipts() -> None:
    try:
        doc = json.loads(RECEIPT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if isinstance(doc, dict):
        with _receipt_lock:
            for k, v in doc.items():
                if isinstance(v, dict):
                    _receipts[str(k)] = v


def _record_receipt(key: str | None, rec: dict[str, Any]) -> None:
    """按 dedup_key 记一张投递回执（内存 + 落盘，最近 200 条）。"""
    if not key:
        return
    with _receipt_lock:
        _receipts[str(key)] = rec
        while len(_receipts) > RECEIPT_KEEP:
            _receipts.pop(next(iter(_receipts)))
    _save_receipts()


def receipt(key: str) -> dict[str, Any] | None:
    """查一张投递回执（None = 还没处理到）。"""
    with _receipt_lock:
        rec = _receipts.get(str(key))
    return dict(rec) if isinstance(rec, dict) else None


def read_health() -> dict[str, Any] | None:
    try:
        h = json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
        return h if isinstance(h, dict) else None
    except (OSError, ValueError):
        return None


def status() -> dict[str, Any]:
    cfg = load_config()
    h = read_health() or {}
    return {
        "ok": True,
        "channel": "qq",
        "enabled": bool(cfg.get("enabled")),
        "configured": _configured(cfg),
        "source": cfg.get("source"),
        "queued": _q.qsize(),
        "sent": _stats["sent"],
        "failed": _stats["failed"],
        "dropped": _stats["dropped"],
        "last_ok_ts": h.get("last_ok_ts"),
        "last_fail_ts": h.get("last_fail_ts"),
        "consecutive_fails": h.get("consecutive_fails", 0),
        "last_error": h.get("last_error"),
        "last_msg_id": h.get("last_msg_id"),
        "last_msg_ts": h.get("last_msg_ts"),
        "origin": h.get("origin"),
    }


# ---------------------------------------------------------------- 队列

def _prune_dedup(now: float) -> None:
    for k in [k for k, ts in _dedup.items() if now - ts >= DEDUP_WINDOW]:
        _dedup.pop(k, None)


def enqueue(event: str, title: str, body: str = "", *, dedup_key: str | None = None,
            level: str = "info") -> dict[str, Any]:
    """入队一条预警（非阻塞）。返回 {ok, reason?, queued}。

    - disabled / dedup 时返回 ok=False，不抛异常（调用方在行情线程里，不能死）。
    - dedup_key 默认 event:title；300s 窗口内同 key 只发一次。
    - 返回里的 `key` 是投递回执的查询键：调用方可用 `receipt(key)` 拿落地 msg_id。
    """
    cfg = load_config()
    if _env_disabled():
        return {"ok": False, "reason": "disabled_env"}
    if not cfg.get("enabled") or not _configured(cfg):
        return {"ok": False, "reason": "disabled"}
    key = dedup_key or ("%s:%s" % (event, title))
    now = time.time()
    with _dedup_lock:
        last = _dedup.get(key)
        if last is not None and now - last < DEDUP_WINDOW:
            return {"ok": False, "reason": "dedup"}
        _dedup[key] = now
        _prune_dedup(now)
    item = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "level": level, "event": event, "title": title, "body": body, "key": key,
    }
    try:
        _q.put_nowait(item)
    except queue.Full:
        try:
            _q.get_nowait()
        except queue.Empty:
            pass
        _stats["dropped"] += 1
        try:
            _q.put_nowait(item)
        except queue.Full:
            return {"ok": False, "reason": "queue_full"}
    start()
    return {"ok": True, "queued": _q.qsize(), "key": key}


def _worker() -> None:
    while True:
        item = _q.get()
        try:
            res = send_qq(item["title"], item["body"]) or {}
            msg_id = str(res.get("msg_id") or "")
            _stats["sent"] += 1
            _record_health(True, info=res)
            _record_receipt(item.get("key"), {
                "status": "confirmed" if msg_id else "sent",
                "msg_id": msg_id or None,
                "msg_ts": res.get("msg_ts") or None,
                "sent_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "origin": ORIGIN,
            })
            _append_log(item, result=res)
        except Exception as exc:  # noqa: BLE001 - 单条失败不影响后续
            _stats["failed"] += 1
            _record_health(False, exc)
            _record_receipt(item.get("key"), {
                "status": "failed", "msg_id": None, "msg_ts": None,
                "sent_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "error": str(exc)[:200], "origin": ORIGIN,
            })
            _append_log(item, str(exc))
            logger.warning("notify 发送失败: %s", exc)
        finally:
            _q.task_done()


def start() -> None:
    """启动 sender 线程（幂等）。main.py startup 调用；enqueue 首次调用也会兜底拉起。"""
    global _sender
    with _sender_lock:
        if _sender is not None and _sender.is_alive():
            return
        _sender = threading.Thread(target=_worker, daemon=True, name="panel-notify")
        _sender.start()


def test_send() -> dict[str, Any]:
    """同步发一条测试消息（/api/notify/test 专用；唯一会真发的入口，需二次确认）。"""
    title = "【测试】面板推送往返核验"
    body = ("这是一条来自作战面板 v2 的**测试消息**，用于核验送达回执（msg_id）。"
            "收到即代表通道通，无需处理。（%s）" % time.strftime("%Y-%m-%d %H:%M:%S"))
    try:
        res = send_qq(title, body) or {}
    except Exception as exc:  # noqa: BLE001 - API 层统一包装
        _stats["failed"] += 1
        _record_health(False, exc)
        return {"ok": False, "detail": str(exc)[:300]}
    _stats["sent"] += 1
    _record_health(True, info=res)
    _append_log({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "level": "info",
                 "event": "NOTIFY_TEST", "title": title, "body": body, "key": "test"},
                result=res)
    msg_id = str(res.get("msg_id") or "")
    return {"ok": True, "status": "confirmed" if msg_id else "sent",
            "msg_id": msg_id or None, "msg_ts": res.get("msg_ts") or None,
            "origin": ORIGIN}


def recent(n: int = 50) -> list[dict[str, Any]]:
    """最近 n 条推送日志（新→旧）；文件不存在返回空。"""
    try:
        lines = LOG_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines[-max(1, min(500, int(n))):]:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return list(reversed(out))


_load_receipts()
