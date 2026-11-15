"""后台任务手动触发门面（`ctx.ops_run`，2026-10-24 N2 追补）。

## 为什么放在 core

`POST /api/ops/run` 的旧语义是 `ops.ops_status.run_manual(name)` → `schtasks /run` 起
Windows 计划任务，属**跨进程系统操作**。插件沙箱静态禁子进程（`tools/cutover_gate.py`
扫 `app/plugins/**`），而这条能力又要保住「零功能损失」，所以：

- 能力放 core（gate 不扫 core，先例：`main.py` 的 `/api/shell/restart` 也在 core 拉进程）；
- 插件只拿 `ctx.ops_run(name)` 这个门面，看不见 `subprocess`；
- **语义单一事实源留在旧仓**：本模块 `importlib` 动态加载
  `engine/ops/ops_status.py` 并调它的 `run_manual(name)`，**不复制**那里的
  `MANUAL_WHITELIST` / 冷却窗口 / 任务状态判定 / 跨进程文件锁——否则白名单一改就是双源漂移，
  而且是「新端能触发旧端拒绝的任务」这种最坏方向的漂移。

## 边界

- 只暴露 `run(name, timeout_s=30)` 一个函数；
- 不做任何二次校验（白名单判定交给旧仓，避免两套规则打架）；
- 旧仓文件不存在 / 加载失败 → 抛 `OpsRunError`（不静默降级成 `ok:false`，避免
  「看起来在跑其实没跑」）；
- 不写任何文件：`run_manual` 自己负责 `ops_manual.log` 与冷却状态。

## 与旧端的差异（如实登记）

- 旧端 HTTP handler 同步等 `run_manual` 返回；这里同一路径，但**多一层 30s 硬上限**：
  `run_manual` 内部 `schtasks` 超时 25s、文件锁 5s，正常路径秒回；30s 是「卡死也不拖住
  请求线程」的网。注意 `timeout_s` 到点只返回失败，**已经在跑的那次调用不会被杀**
  （Windows 上强杀 `schtasks` 子进程可能留下半提交态，比多发一条日志更糟）。
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core import paths

# 旧仓聚合器（语义唯一源）。路径按 paths.WS 推导，不写死盘符。
LEGACY_OPS_STATUS = paths.WS / "engine" / "ops" / "ops_status.py"
# M1 补缺的三个固定动作的旧仓实现位置（都是**同进程 Python 调用**，不是 schtasks）：
#   amv_refresh      → executor/amv_live.py::run_once(publish=True)（旧 server.py:2247 同款）
#   sector_backfill  → engine/tools/backfill_sector_track.py::run(...)（旧 server.py:2307/2320）
LEGACY_AMV_LIVE = paths.WS / "engine" / "executor" / "amv_live.py"
LEGACY_SECTOR_BACKFILL = paths.WS / "engine" / "tools" / "backfill_sector_track.py"

# 固定动作白名单（名字 → 说明）。**只这三个**，不接任意名字：
# 旧端这三个端点也没有参数名，端口写在函数体里；白名单只是把「谁能被调」写成一张表，
# 避免以后有人把 `run_action` 当成第二个 `run_manual` 乱加名字。
OPS_ACTIONS: dict[str, str] = {
    "amv_refresh": "按需刷新实时活跃市值（amv_live.run_once(publish=True)，锁+20s 超时）",
    "sector_backfill": "板块轨迹缺口补齐（backfill_sector_track.run，纯函数；有缺口转后台线程 apply）",
    # 2026-10-30：`sector_restart` 已整条摘除（8002 下线，文件已销毁），不再保留 410 桩。
    # 2026-10-26 旧端口下线补位：`/api/ops.json?refresh=1` 的真实重算口。
    # 改前 8800 的 GET /api/ops.json 每次都现场 build；v2 的 ops 插件只读快照，
    # 不补这一条的话后台 Tab 会一直显示上一夜的快照（追问题时会误导）。
    # 语义单源仍是 `engine/ops/ops_status.py::build(force=True, write=True)`，本层只调用。
    "ops_snapshot": "重算后台监控快照 ops_status.json（ops_status.build(force=True, write=True)）",
}

# 旧仓模块名（sys.modules 里用它保证只加载一次）
_MODULE_NAME = "_panel_v2_legacy_ops_status"

_CACHE: dict[str, Any] = {}


class OpsRunError(Exception):
    """旧仓聚合器不可用（文件缺失 / 导入失败）—— 调用方必须上报，不得当作「已触发」。"""


def reset_cache() -> None:
    """清掉已加载的旧仓模块（测试隔离用；生产不需要调）。"""
    _CACHE.pop("module", None)
    sys.modules.pop(_MODULE_NAME, None)


def _load_legacy() -> Any:
    """加载旧仓 `ops_status` 模块（按 (路径) 缓存）。

    单独一个函数是为了**可替换**：测试 monkeypatch 本函数注入假 `run_manual`，
    从而完全避开真的 `schtasks`。
    """
    cached = _CACHE.get("module")
    if cached is not None:
        return cached
    path = LEGACY_OPS_STATUS
    if not Path(path).is_file():
        raise OpsRunError("旧仓聚合器不存在：%s（手动触发依赖它，未降级）" % path)
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, path)
    if spec is None or spec.loader is None:
        raise OpsRunError("无法为 %s 建立 import spec" % path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - 导入失败一律上报，不静默
        sys.modules.pop(_MODULE_NAME, None)
        raise OpsRunError("旧仓聚合器导入失败：%s: %s" % (type(exc).__name__, exc)) from exc
    if not callable(getattr(module, "run_manual", None)):
        sys.modules.pop(_MODULE_NAME, None)
        raise OpsRunError("旧仓聚合器缺少 run_manual()：%s" % path)
    _CACHE["module"] = module
    return module


def run(name: str, timeout_s: float = 30.0) -> dict[str, Any]:
    """手动触发一个数据类计划任务（`name` = 计划任务名）。

    返回旧仓 `run_manual` 的原样 dict（`{ok, reason, task, ts, message}` 或其失败变体），
    不加工、不补键——旧前端口径由旧仓单源决定。

    `name` 只做类型/空值挡板；**白名单判定不在这里做**（见模块头）。
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("ops_run 的任务名必须是非空字符串")
    name = name.strip()

    module = _load_legacy()

    # `run_manual` 内部 `from common.filelock import ...`，需要 engine 目录在 sys.path。
    # 旧仓模块在 import 期自己插了一条；这里只补「还没在」的情况，调用后原样收回，
    # 不把旧仓目录永久留在 sys.path（同 contract.lib 的处理口径）。
    engine_dir = str(paths.WS / "engine")
    inserted = engine_dir not in sys.path
    if inserted:
        sys.path.append(engine_dir)

    box: dict[str, Any] = {}

    def _call() -> None:
        try:
            box["result"] = module.run_manual(name)
        except Exception as exc:  # noqa: BLE001 - 交给主线程统一处理
            box["error"] = exc

    try:
        worker = threading.Thread(target=_call, name="panel-v2-ops-run", daemon=True)
        worker.start()
        worker.join(timeout_s)
        if worker.is_alive():
            # 不 kill：强杀 schtasks 可能留下半提交态。如实上报超时。
            return {
                "ok": False,
                "reason": "timeout",
                "task": name,
                "message": "手动触发超过 %gs 未返回（底层调用可能仍在进行，稍后看任务状态）"
                           % float(timeout_s),
            }
    finally:
        if inserted:
            sys.path[:] = [p for p in sys.path if p != engine_dir]

    if "error" in box:
        exc = box["error"]
        return {
            "ok": False,
            "reason": "error",
            "task": name,
            "message": "%s: %s" % (type(exc).__name__, exc),
        }
    result = box.get("result")
    if not isinstance(result, dict):
        return {
            "ok": False,
            "reason": "bad_response",
            "task": name,
            "message": "旧仓 run_manual 返回了 %s（预期 dict）" % type(result).__name__,
        }
    return result


# ============================================================================
# 固定动作通道（M1 补缺：amv_refresh / sector_backfill）
#
# 为什么与 `run()` 分开：`run()` 是「计划任务名→schtasks」，白名单与冷却都在旧仓；
# 这三条是**旧 server.py 里的固定处理函数**，没有参数名也没有 schedule，
# 锁/超时/结果形状都是旧 handler 自己定的（见每个函数的 docstring）。
# 但性质相同：插件不能自己起进程/线程，所以仍然只能在 core 里实现。
#
# 共用边界：
#   • 只认 `OPS_ACTIONS` 里的三个名字，其余 ValueError（调用方 400）；
#   • 重活一律丢后台线程 + join 上限，到点如实返回超时（**不杀已在跑的那次调用**）；
#   • 旧仓文件缺失/导入失败 → `OpsRunError`（上层 503，不谎报「已触发」）。
# ============================================================================

# 旧端常量（`engine/server.py:2232 AMV_REFRESH_TIMEOUT_S`）
AMV_REFRESH_TIMEOUT_S = 20.0

_ACTION_LOCKS: dict[str, threading.Lock] = {"amv_refresh": threading.Lock(),
                                            "sector_backfill": threading.Lock()}
_ACTION_LOCKS_GUARD = threading.Lock()


def _action_lock(name: str) -> threading.Lock:
    with _ACTION_LOCKS_GUARD:
        lock = _ACTION_LOCKS.get(name)
        if lock is None:
            lock = _ACTION_LOCKS[name] = threading.Lock()
        return lock


def _ensure_legacy_path() -> list[str]:
    """幂等把旧仓的四个 import 根加入 sys.path（进程级一次，**不恢复**）。

    为什么必须永久而非临时：旧仓工具里的 import 是**函数体内延迟 import** 的
    （`backfill_sector_track.replay()` 里 `from executor import amv_live`，
    `amv_live` 又 `import amv_store` + `numpy`），发生在模块加载完成之后。
    临时插-恢复（口径库 `ctx.lib` 那套）在这里会漏：模块 import 成功，但真正跑的时候
    `executor` 已经不在 path 上了 → `ModuleNotFoundError`。2026-10-24 15:13 在 8811 实测踩到。

    旧进程本来就是这么干的：`engine/server.py` 在 import 期 `sys.path.insert(0, engine)`，
    `amv_live` 在 import 期插 `SUMMER_TOOLS_DIR`。子进程版（旧 `subprocess.run(cwd=WORKSPACE)`）
    也天然带这三个目录。本函数把同一环境补回同进程，不新增能力。

    边界：只在 `run_action()` 真正要跑旧仓工具时调用（不是 core 启动就插），
    且四个目录全部是旧仓自己的 import 根，不引入第三方路径。
    """
    roots = (
        str(paths.WS / "engine" / "executor"),
        str(paths.WS / "engine"),
        str(paths.WS / "夏天-activity" / "工具"),
        str(paths.WS / "envs" / "qmt312" / "Lib" / "site-packages"),
    )
    added: list[str] = []
    for d in roots:
        if d not in sys.path:
            sys.path.append(d)
            added.append(d)
    return added


def _load_module(path: Path, module_name: str, required: tuple[str, ...]) -> Any:
    """加载旧仓一个模块（按路径缓存），校验必需符号。缺失/导入失败 → OpsRunError。"""
    cached = _CACHE.get(module_name)
    if cached is not None:
        return cached
    if not Path(path).is_file():
        raise OpsRunError("旧仓模块不存在：%s" % path)
    _ensure_legacy_path()
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise OpsRunError("无法为 %s 建立 import spec" % path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - 导入失败如实上报
        sys.modules.pop(module_name, None)
        raise OpsRunError("旧仓模块导入失败（%s）：%s: %s"
                          % (Path(path).name, type(exc).__name__, exc)) from exc
    for sym in required:
        if not callable(getattr(module, sym, None)):
            sys.modules.pop(module_name, None)
            raise OpsRunError("旧仓模块缺少 %s()：%s" % (sym, path))
    _CACHE[module_name] = module
    return module


def _in_thread(fn: Any, timeout_s: float, thread_name: str) -> tuple[bool, dict[str, Any]]:
    """在后台线程跑 fn，join 上限 timeout_s。返回 (是否还在跑, 箱)。

    与 `run()` 的铁律一致：**到点不 kill**。旧端 `api_amv_refresh` 也是这个写法
    （`t.join(20)` 后 `t.is_alive()` → 504，后台继续）；强杀会把半发布状态留给前端。
    """
    box: dict[str, Any] = {}

    def _call() -> None:
        try:
            box["result"] = fn()
        except Exception as exc:  # noqa: BLE001 - 交给主线程统一处理
            box["error"] = exc

    worker = threading.Thread(target=_call, name=thread_name, daemon=True)
    worker.start()
    worker.join(timeout_s)
    return worker.is_alive(), box


def _amv_refresh() -> dict[str, Any]:
    """按需刷新实时活跃市值（旧 `api_amv_refresh`，`engine/server.py:2235`）。

    旧链路：`executor.amv_live.run_once(publish=True)`，重活 1-2s，锁（非阻塞，连点 429）+ 20s join。
    返回形状直接取 `run_once` 的 payload（旧端从 payload 抽 5 个字段），
    无新数据（非交易时段/行情陈旧/闸门未过）→ `ok:false` + reason（旧端同文案）。
    """
    lock = _action_lock("amv_refresh")
    if not lock.acquire(blocking=False):
        return {"ok": False, "status": "busy", "http": 429,
                "detail": "上一次刷新仍在进行，请稍候"}

    def _job() -> Any:
        try:
            module = _load_module(LEGACY_AMV_LIVE, "_panel_v2_legacy_amv_live", ("run_once",))
            return module.run_once(publish=True)
        finally:
            lock.release()

    alive, box = _in_thread(_job, AMV_REFRESH_TIMEOUT_S, "panel-v2-amv-refresh")
    if alive:
        return {"ok": False, "status": "timeout", "http": 504,
                "detail": "刷新超时（>%.0fs），后台仍在跑" % AMV_REFRESH_TIMEOUT_S}
    if "error" in box:
        exc = box["error"]
        if isinstance(exc, OpsRunError):
            return {"ok": False, "status": "unavailable", "http": 503, "detail": str(exc)}
        return {"ok": False, "status": "error", "http": 500,
                "detail": "刷新失败：%s: %s" % (type(exc).__name__, exc)}
    p = box.get("result")
    if not isinstance(p, dict):
        # 旧端同：payload=None → 读现有 amv_live.json 回当前值 + ok:false（不写盘）
        cur = _read_panel_json("amv_live") or {}
        return {"ok": False, "reason": "无新数据（非交易时段/行情陈旧/闸门未过）",
                "asof": cur.get("asof"), "all": cur.get("all"),
                "coverage": cur.get("coverage"), "stale": cur.get("stale", False)}
    return {"ok": True, "asof": p.get("asof"), "all": p.get("all"),
            "coverage": p.get("coverage"), "stale": p.get("stale", False),
            "indices": len(p.get("indices") or [])}


def _read_panel_json(name: str) -> dict[str, Any] | None:
    """core 内部直读 data/panel 白名单 JSON（仅 amv_refresh 的降级分支用）。"""
    path = paths.panel_path(name)
    if path is None or not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _backfill_status_running() -> bool:
    """板块回填状态文件 → 是否仍在跑（旧 `_bf_running`，`engine/server.py:2282`）。

    30 分钟超时保护：进程异常死亡时 status 会永远停在 running，靠 ts 判死。
    状态文件在 `engine/state/`（不在白名单）——core 直读是旧端同路径，与插件写通道无关。
    """
    path = paths.STATE_DIR / "sector_track_backfill_status.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        return False
    if not isinstance(st, dict) or st.get("status") != "running":
        return False
    try:
        ts = datetime.strptime(str(st.get("ts", "")), "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return False
    return (datetime.now() - ts).total_seconds() < 1800


def _sector_backfill() -> dict[str, Any]:
    """检查板块轨迹缺口；有缺口则后台跑补录（旧 `api_sector_track_backfill`，`engine/server.py:2292`）。

    旧端是子进程（`--check-only` 同步 + `Popen --apply` 异步）。这里调 **同一份工具的
    Python 函数** `backfill_sector_track.run(check_only=...)`：
    · `--check-only` 等价 = `run(check_only=True)`（不下载、不写状态）；
    · `--apply` 等价 = 后台线程跑 `run(apply=True)`（工具自己写 status 文件，前端轮询
      `/api/sector_track_backfill_status` 不变）。
    返回形状照旧：`{ok, status: ok|started|busy|error, gaps, points?, detail?}`。
    """
    lock = _action_lock("sector_backfill")
    if _backfill_status_running():
        return {"ok": True, "status": "busy", "detail": "上一次补齐仍在进行"}
    if not lock.acquire(blocking=False):
        return {"ok": False, "status": "busy", "detail": "检查进行中，请稍后"}
    try:
        try:
            module = _load_module(LEGACY_SECTOR_BACKFILL, "_panel_v2_legacy_backfill", ("run",))
        except OpsRunError as exc:
            return {"ok": False, "status": "error", "detail": str(exc)}
        try:
            res = module.run(check_only=True) or {}
        except Exception as exc:  # noqa: BLE001 - 检查失败不拖垮面板（旧端同：status=error）
            return {"ok": False, "status": "error", "detail": "%s: %s" % (type(exc).__name__, exc)}
        gaps = res.get("gaps") or []
        if not gaps:
            return {"ok": True, "status": "ok", "gaps": [], "points": res.get("points_before")}

        def _apply() -> None:
            try:
                module.run(apply=True)
            except Exception:  # noqa: BLE001 - 后台失败只留痕（工具自己写 status=error）
                pass

        threading.Thread(target=_apply, name="panel-v2-sector-backfill", daemon=True).start()
        return {"ok": True, "status": "started", "gaps": gaps}
    finally:
        lock.release()




def _ops_snapshot() -> dict[str, Any]:
    """重算后台监控快照（2026-10-26 旧端口下线补位）。

    改前：`GET /api/ops.json` 在 8800 里每次都调 `ops_status.build(force=True, write=True)`
    现场重算并落盘，所以后台 Tab 一开就是新鲜状态。8800 退役后 v2 的 ops 插件只读快照，
    不补这条，快照就只在夜间链跑过一次 → 白天追问题时看到的是上一夜的状态（误导）。

    语义单源：`engine/ops/ops_status.py::build(force=True, write=True)`（= 它 `__main__`
    里的那行，也是原 8800 的调用）；本函只负责加载与并发挡板，不重算任何指标。
    耗时实测 ~5s（扫 schtasks/WMI），故只供「刷新」按钮按需触发，不挂自动轮询。
    """
    lock = _action_lock("ops_snapshot")
    if not lock.acquire(blocking=False):
        return {"ok": False, "status": "busy", "http": 429,
                "detail": "上一次重算仍在进行，请稍候"}
    try:
        # `ops_status` 内部会 `from common.* import ...`（函数级 import），故 engine 目录要
        # 全程在 sys.path 上——包括 build() 执行期，不只是 import 期（早收一步就 ModuleNotFoundError）。
        engine_dir = str(paths.WS / "engine")
        inserted = engine_dir not in sys.path
        if inserted:
            sys.path.append(engine_dir)
        try:
            module = _load_legacy()
            build = getattr(module, "build", None)
            if not callable(build):
                return {"ok": False, "status": "error", "http": 503,
                        "detail": "旧仓聚合器缺少 build()：%s" % LEGACY_OPS_STATUS}
            data = build(force=True, write=True)
        finally:
            if inserted:
                try:
                    sys.path.remove(engine_dir)
                except ValueError:
                    pass
        meta = (data or {}).get("meta") or {}
        return {"ok": True, "status": "ok", "updated_at": meta.get("updated_at"),
                "summary": (data or {}).get("summary")}
    except OpsRunError as exc:
        return {"ok": False, "status": "error", "http": 503, "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001 - 重算失败如实报，不谎报已刷新
        return {"ok": False, "status": "error", "http": 500,
                "detail": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        lock.release()


_ACTION_IMPL = {
    "amv_refresh": _amv_refresh,
    "sector_backfill": _sector_backfill,
    "ops_snapshot": _ops_snapshot,
}


def run_action(name: str) -> dict[str, Any]:
    """执行一个**固定动作**（白名单见 `OPS_ACTIONS`）；未知名字 → ValueError。

    与 `run()` 的差异只在「触发什么」：`run()` 是计划任务名（旧仓定白名单），
    这里只有三个旧端 handler 的固定动作。返回值是各自 handler 的旧形状（含 `ok`），
    需映射成 HTTP 状态的带 `http` 键（插件层负责翻译）。
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("ops 动作名必须是非空字符串")
    key = name.strip()
    impl = _ACTION_IMPL.get(key)
    if impl is None:
        raise ValueError("未登记的 ops 动作：%s（可选 %s）" % (key, "/".join(sorted(_ACTION_IMPL))))
    return impl()


# ============================================================================
# 按需脚本（2026-11-04）：面板上“点一下重跑某个生成脚本”
#
# 为什么在 core：插件侧有静态扫描禁令（gate 扫 `subprocess.` / `Popen(`），
#   而“跑一个脚本”本质上是运维动作，与 `/api/shell/restart` 同类，只能 core 起进程。
# 语义源仍在旧仓：本层只按名字去 `ops_status.MANUAL_SCRIPTS` 取路径，不硬编码业务路径。
# 并发：同名脚本同一时刻只跑一份（连点不会拉起两份 140 秒联网任务）。
# ============================================================================

#: 脚本硬上限（秒）。生成类脚本正常 110~140 秒，300 秒是“卡死也不拖住”的网。
SCRIPT_TIMEOUT_S = 300

#: 脚本输出落地（每次触发追加一段）。没它就会出现“点了没反应、又看不出为什么”。
SCRIPT_LOG = paths.WS / "OH-Works" / "定时日志" / "ops_script.log"

_SCRIPT_LOCKS: dict[str, threading.Lock] = {}
_SCRIPT_GUARD = threading.Lock()


def run_script(name: str) -> dict[str, Any]:
    """后台跑一个按需脚本（白名单 = 旧仓 `MANUAL_SCRIPTS`）。

    白名单条目形状：`{python, args, cwd, timeout_s}`（也兼容直接给一个 .ps1 路径的旧写法）。
    未知名字 → `ValueError`（调用方 400）；旧仓不可用 / 脚本缺失 → `OpsRunError`（503）；
    已在跑 → `{"ok": False, "reason": "busy"}`（不是错误，前端提示即可）。
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("脚本名必须是非空字符串")
    key = name.strip()
    module = _load_legacy()
    scripts = getattr(module, "MANUAL_SCRIPTS", None) or {}
    spec_raw = scripts.get(key)
    if not spec_raw:
        raise ValueError("未登记的按需脚本：%s（可选 %s）" % (key, "/".join(sorted(scripts))))

    if isinstance(spec_raw, str):            # 旧写法：一个 PowerShell 脚本路径
        cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", spec_raw]
        cwd = str(paths.WS)
        timeout_s = SCRIPT_TIMEOUT_S
        must_exist = spec_raw
    else:
        python = spec_raw.get("python") or sys.executable
        args = list(spec_raw.get("args") or [])
        cmd = [python] + args
        cwd = str(spec_raw.get("cwd") or paths.WS)
        timeout_s = int(spec_raw.get("timeout_s") or SCRIPT_TIMEOUT_S)
        must_exist = os.path.join(cwd, args[-1]) if args else ""
    if must_exist and not Path(must_exist).is_file():
        raise OpsRunError("脚本不存在：%s" % must_exist)

    with _SCRIPT_GUARD:
        lock = _SCRIPT_LOCKS.setdefault(key, threading.Lock())
    if not lock.acquire(blocking=False):
        return {"ok": False, "name": key, "reason": "busy", "detail": "上一次还没跑完"}

    def _worker() -> None:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        head = "[%s] %s -> %s (cwd=%s)" % (stamp, key, " ".join(cmd[-2:]), cwd)
        tail: list[str] = []
        code: Any = None
        try:
            # 流式读：既落日志（出问题能看见卡在哪一步），又不会因管道写满而肚死。
            proc = subprocess.Popen(
                cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                creationflags=0x08000000,   # CREATE_NO_WINDOW：不起黑框
            )
            deadline = time.time() + timeout_s
            assert proc.stdout is not None
            for line in proc.stdout:
                tail.append(line.rstrip())
                if len(tail) > 200:
                    del tail[0]
                if time.time() > deadline:
                    proc.kill()
                    code = "timeout"
                    break
            if code != "timeout":
                code = proc.wait(timeout=30)
        except Exception as exc:  # noqa: BLE001
            code = "EXC %r" % (exc,)
        finally:
            try:
                SCRIPT_LOG.parent.mkdir(parents=True, exist_ok=True)
                with SCRIPT_LOG.open("a", encoding="utf-8") as f:
                    f.write("%s exit=%s\n%s\n\n" % (head, code, "\n".join(tail[-30:]) or "(no output)"))
            except Exception:  # noqa: BLE001 - 日志写不了也不能把锁卡死
                pass
            lock.release()

    threading.Thread(target=_worker, daemon=True).start()
    return {"ok": True, "name": key, "started": True}

    threading.Thread(target=_worker, daemon=True).start()
    return {"ok": True, "name": key, "started": True}

