# -*- coding: utf-8 -*-
"""盘后预热：数据就绪后，后台先把各页首屏结果算一遍，用户打开时直接命中。

为什么（2026-11-07 主人定）：
    收盘数据是算好的，用户不该再等「第一次打开」的 6-10 秒。
    链跑完会把状态写进后台看板（`data/panel/ops_status.json`，链末 opssnap 步）；
    这里读看板判断「数据日到了、关键源都新鲜」，再把首屏端点跑一遍，让落盘缓存先热。
    看板没就绪就不预热——不把昨天当今天。

边界：
    · 只 GET 清单里登记的只读端点；清单在 `data/panel_v2/prewarm_plan.json`，
      core 不硬编码任何业务路径。
    · 单任务失败只记录、不影响其他；同一数据日最多重试 MAX_RETRY 次（指数退避）。
    · 不写生产数据、不改链、不新建常驻服务（线程随 app 生命周期，daemon）。
    · 只用于「知不知道数据好了」，**绝不参与链自己判断跑不跑**。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import httpx

from app.core import paths, trade_clock

PLAN_NAME = "prewarm_plan.json"
STATE_NAME = "prewarm_state.json"
POLL_S = 60.0              # 看板探测周期
MAX_RETRY = 3
RETRY_BACKOFF_S = 300.0
CONCURRENCY = 2
HTTP_TIMEOUT_S = 180.0

_log = logging.getLogger("panel_v2.prewarm")
_STOP = threading.Event()
_THREAD: Optional[threading.Thread] = None
_APP: Any = None
_LOCK = threading.Lock()
# 预热是“让**本进程**的缓存先热”，跨进程的 done 记录不能信（新进程内存是空的）；
# 用 pid 判别：pid 不同 → 视为未预热，全量重跑。
_BOOT_PID = os.getpid()


# ---------------------------------------------------------------- 读清单 / 看板


def plan_path() -> Path:
    """预热清单随代码走（不放在 data/ 运行时目录，避免清数据时丢掉）。"""
    return paths.PANEL_V2_DIR / PLAN_NAME


def state_path() -> Path:
    return paths.OUT_DIR / STATE_NAME


def load_plan() -> dict[str, Any]:
    """读预热清单；缺失/损坏 → 空计划（不预热，不报错）。"""
    p = plan_path()
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 首次/损坏 → 不预热
        return {}
    if not isinstance(doc, dict):
        return {}
    tasks = []
    for t in (doc.get("tasks") or []):
        if not isinstance(t, dict):
            continue
        path = str(t.get("path") or "")
        if not path.startswith("/api/"):
            continue
        task = {"path": path, "timeout": float(t.get("timeout") or HTTP_TIMEOUT_S)}
        spec = t.get("codes_from")
        if isinstance(spec, dict):
            task["codes_from"] = spec
        tasks.append(task)
    sources = [str(s) for s in (doc.get("ready_sources") or []) if str(s)]
    return {"version": doc.get("version"), "ready_sources": sources, "tasks": tasks}


def read_board() -> dict[str, Any]:
    """读后台看板（ops_status.json）；缺失/损坏 → 空。"""
    p = paths.panel_path("ops")
    if p is None or not p.is_file():
        return {}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return doc if isinstance(doc, dict) else {}


def readiness(board: dict[str, Any], plan: dict[str, Any],
              expected8: str) -> tuple[bool, str]:
    """数据是否就绪：看板数据日 == 期望日，且登记的关键数据源全部「新鲜」。"""
    meta = board.get("meta") or {}
    td = str(meta.get("trade_date") or "").replace("-", "")
    if not td:
        return False, "看板缺 trade_date"
    if td != expected8:
        return False, "看板数据日 %s != 期望 %s" % (td, expected8)
    fresh: dict[str, str] = {}
    for row in (board.get("freshness") or []):
        if isinstance(row, dict):
            fresh[str(row.get("key"))] = str(row.get("state") or "")
    missing = [k for k in plan.get("ready_sources") or [] if fresh.get(k) != "新鲜"]
    if missing:
        return False, "未新鲜：%s" % ",".join(missing)
    return True, ""


# ---------------------------------------------------------------- 状态与待跑任务


def _load_state() -> dict[str, Any]:
    try:
        doc = json.loads(state_path().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return doc if isinstance(doc, dict) else {}


def _save_state(state: dict[str, Any]) -> None:
    try:
        state_path().parent.mkdir(parents=True, exist_ok=True)
        tmp = state_path().with_name(state_path().name + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(state_path())
    except Exception as exc:  # noqa: BLE001 - 状态写失败不影响预热本身
        _log.warning("prewarm state write failed: %s", exc)


def tasks_to_run(state: dict[str, Any], plan: dict[str, Any],
                 expected8: str, now_ts: Optional[float] = None) -> list[dict[str, Any]]:
    """按状态挑出本轮该跑的任务。

    · 数据日变了 → 全量重来（清空 done/failed）
    · 同一数据日 → 跳过已成功的；失败过的到退避时间才重试，超过上限放弃
    """
    now_ts = time.time() if now_ts is None else now_ts
    same_day = (str(state.get("expected") or "") == expected8
                and int(state.get("pid") or 0) == _BOOT_PID)
    done = dict(state.get("done") or {}) if same_day else {}
    failed = dict(state.get("failed") or {}) if same_day else {}
    out = []
    for task in plan.get("tasks") or []:
        path = task["path"]
        if path in done:
            continue
        rec = failed.get(path) or {}
        if int(rec.get("count") or 0) >= MAX_RETRY:
            continue
        if float(rec.get("next_at") or 0) > now_ts:
            continue
        out.append(task)
    return out


# ---------------------------------------------------------------- 执行


def _expand_codes(task: dict[str, Any]) -> list[str]:
    """按清单里的 `codes_from` 从生产 panel JSON 取代码（通用遍历，不含业务常量）。

    规则写在清单里：panel 名 + 数组字段 + 代码字段 + 过滤条件 + 上限。
    读不到/结构不符 → 空列表（该任务本轮跳过，不报错）。
    """
    spec = task.get("codes_from")
    if not isinstance(spec, dict):
        return []
    p = paths.panel_path(str(spec.get("panel") or ""))
    if p is None or not p.is_file():
        return []
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 坏文件当没有
        return []
    rows = doc.get(str(spec.get("items") or "items")) if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        return []
    key = str(spec.get("key") or "code")
    where = spec.get("where") or {}
    maxn = max(1, int(spec.get("max") or 60))
    out: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if any(row.get(k) != v for k, v in where.items()):
            continue
        code = str(row.get(key) or "").strip()
        if code:
            out.append(code)
        if len(out) >= maxn:
            break
    return out


def _prepare(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把带 `{codes}` 的模板展开成真实路径；结果里保留模板 path 作为状态键。"""
    out: list[dict[str, Any]] = []
    for task in tasks:
        path = task["path"]
        if "{codes}" in path:
            codes = _expand_codes(task)
            if not codes:
                continue
            path = path.replace("{codes}", ",".join(codes))
        out.append({"id": task["path"], "path": path,
                    "timeout": task.get("timeout") or HTTP_TIMEOUT_S})
    return out


async def _run_async(app: Any, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    transport = httpx.ASGITransport(app=app)
    sem = asyncio.Semaphore(CONCURRENCY)

    async with httpx.AsyncClient(transport=transport, base_url="http://panel.local",
                                 timeout=httpx.Timeout(HTTP_TIMEOUT_S)) as cli:
        async def one(task: dict[str, Any]) -> dict[str, Any]:
            async with sem:
                t0 = time.time()
                try:
                    r = await cli.get(task["path"], timeout=task.get("timeout") or HTTP_TIMEOUT_S)
                    return {"path": task.get("id") or task["path"], "ok": 200 <= r.status_code < 300,
                            "status": r.status_code, "ms": int((time.time() - t0) * 1000)}
                except Exception as exc:  # noqa: BLE001 - 单任务失败不拖垮整轮
                    return {"path": task.get("id") or task["path"], "ok": False,
                            "ms": int((time.time() - t0) * 1000),
                            "error": "%s: %s" % (type(exc).__name__, exc)}

        return await asyncio.gather(*(one(t) for t in tasks))


def run_tasks(app: Any, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同步执行一批预热（在线程里跑独立事件循环，不碰主循环）。"""
    try:
        return asyncio.run(_run_async(app, tasks))
    except Exception as exc:  # noqa: BLE001
        _log.warning("prewarm batch failed: %s: %s", type(exc).__name__, exc)
        return []


def tick(app: Any, now: Optional[datetime] = None) -> dict[str, Any]:
    """一个探测周期：读看板 → 判就绪 → 挑任务 → 跑 → 记状态。返回本轮摘要（测试用）。"""
    plan = load_plan()
    if not plan.get("tasks"):
        return {"ran": 0, "ready": False, "why": "无预热清单"}
    board = read_board()
    expected8 = trade_clock.expected_data_date8(now)
    ready, why = readiness(board, plan, expected8)
    if not ready:
        return {"ran": 0, "ready": False, "why": why, "expected": expected8}

    state = _load_state()
    if (str(state.get("expected") or "") != expected8
            or int(state.get("pid") or 0) != _BOOT_PID):
        # 数据日变了，或者重启了（内存缓存已清空）→ 全量重来
        state = {"expected": expected8, "pid": _BOOT_PID, "done": {}, "failed": {},
                 "board_updated_at": ""}
    tasks = _prepare(tasks_to_run(state, plan, expected8))
    if not tasks:
        return {"ran": 0, "ready": True, "why": "已全部预热", "expected": expected8}

    board_at = str((board.get("meta") or {}).get("updated_at") or "")
    t0 = time.time()
    results = run_tasks(app, tasks)
    elapsed = int((time.time() - t0) * 1000)

    done = dict(state.get("done") or {})
    failed = dict(state.get("failed") or {})
    now_s = time.time()
    for r in results:
        path = r["path"]
        if r.get("ok"):
            done[path] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            failed.pop(path, None)
        else:
            rec = dict(failed.get(path) or {})
            rec["count"] = int(rec.get("count") or 0) + 1
            rec["last"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            rec["error"] = r.get("error") or ("HTTP %s" % r.get("status"))
            rec["next_at"] = now_s + RETRY_BACKOFF_S * rec["count"]
            failed[path] = rec

    state.update({"expected": expected8, "pid": _BOOT_PID, "done": done, "failed": failed,
                  "board_updated_at": board_at,
                  "last_run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                  "last_elapsed_ms": elapsed})
    _save_state(state)
    _log.info("prewarm: %d/%d ok, %d ms", sum(1 for r in results if r.get("ok")),
              len(results), elapsed)
    return {"ran": len(results), "ready": True, "expected": expected8,
            "ok": sum(1 for r in results if r.get("ok")), "elapsed_ms": elapsed}


# ---------------------------------------------------------------- 生命周期


def _loop(app: Any) -> None:
    while not _STOP.wait(POLL_S):
        try:
            tick(app)
        except Exception as exc:  # noqa: BLE001 - 后台线程绝不把异常抛出去
            _log.warning("prewarm tick failed: %s: %s", type(exc).__name__, exc)


def start(app: Any) -> None:
    """启动后台预热线程（幂等）。没有清单时线程空转，不产生请求。

    `PANEL_V2_PREWARM=0` 可关（测试/离线环境）；默认开。
    """
    global _THREAD, _APP
    import os

    if os.environ.get("PANEL_V2_PREWARM", "1") == "0":
        return
    with _LOCK:
        _APP = app
        if _THREAD is not None and _THREAD.is_alive():
            return
        _STOP.clear()
        _THREAD = threading.Thread(target=_loop, args=(app,), name="panel-v2-prewarm", daemon=True)
        _THREAD.start()
        _log.info("prewarm thread started (poll %.0fs)", POLL_S)


def stop() -> None:
    """停线程（幂等，wait ≤ 2s）。"""
    global _THREAD
    with _LOCK:
        _STOP.set()
        t = _THREAD
        _THREAD = None
    if t is not None and t.is_alive():
        t.join(2.0)


def status_snapshot() -> dict[str, Any]:
    """只读快照（给 `/api/prewarm/status`）：时间口径 + 清单规模 + 本轮状态。"""
    plan = load_plan()
    return {
        "trade_clock": trade_clock.status(),
        "plan_tasks": len(plan.get("tasks") or []),
        "ready_sources": plan.get("ready_sources") or [],
        "state": _load_state(),
    }
