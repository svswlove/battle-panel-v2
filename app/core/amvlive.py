"""实时活跃市值逐股快照门面（`ctx.amv_live`，2026-10-24）。

## 为什么放在 core

`/api/amv_research.json?live=1` 的旧 8800 语义是：面板按需调
`executor.amv_live.stocks_snapshot()` 拿一次全市场逐股 AMV 快照（线程 + 非阻塞锁 + join 超时，
失败即回退收盘口径）。这条通道有两个插件拿不到的东西：

1. **重模块 import**：`amv_live` 顶层拉 `numpy` / `amv_store` / `common.paths` / `bj_scope`，
   且要求 `engine/` 在 `sys.path` 上。插件禁 `from common` / 禁 `xtquant`（契约静态扫描），
   也不该为了一个端点把 numpy 拉进插件进程。
2. **QMT 取数**：`stocks_snapshot()` 内部走 `xtquant.xtdata.get_full_tick`。插件自己连 QMT
   就是第二套连接与降级分支（CONTRACT §4.1 同一条理由）。

因此能力放 core，插件只拿 `ctx.amv_live` 这个门面（manifest 声明 `data.amv_live: true`）。
**口径零复制**：本模块只负责「什么时候调、多久缓存、失败怎么报」，算路唯一源仍是旧仓
`engine/executor/amv_live.py::stocks_snapshot`（它内部是 `amv_store.step_one_day` v9 递推）。

## 与旧端的对齐（`engine/server.py::_rs_live`，L2765）

- 缓存 TTL：盘中 25s / 盘外 300s（同 `_RS_LIVE_TTL_ON` / `_RS_LIVE_TTL_OFF`）；
- 线程 + `join(6s)`：典型 0.5s；到点不 kill，后台跑完**自己写缓存**（结果不丢），本轮返回上一帧；
- inflight 单飞：上一轮还在算 → 直接返回旧帧，不排队、不叠加 worker（`INFLIGHT_MAX_S` 防卡死）；
- **失败也写缓存**：数据源未开时连点不会每次都重新 import + 打穿数据源；
- 错误文本口径同旧端：`"timeout"` / `"<ExcType>: <msg>"` / `"empty"`（有文档时为 None）。

差异一处：`_RS_LIVE_JOIN_S` 旧端 8s，本模块 6s（任务书指定）。周末 QMT 未开时实测同步调用
约 4.2s 返回 None，6s 覆盖该路径；超时只是提前放弃，后台线程继续跑完（daemon）。

## 边界

- **模块顶层零副作用**：不 import 旧仓、不 import numpy、不起线程。旧仓模块只在
  `snapshot()` 的 worker 线程里懒加载（core 启动不付 0.76s 的 import 成本）。
- `sys.path` 临时插入 `WS/engine` 后**整体还原**（同 `contract.lib()` 的做法），
  不让旧仓目录永久留在 path 上；import 期由旧仓自己插的 `夏天-activity/工具` 一并清掉。
- `stocks_snapshot()` 只读：不落盘、不写轨迹、不动 tick_feed 主进程。
- 返回 `dict | None`，形状与旧仓一致：
  `{date, asof, state_date, all_amv, base_all, stocks:{code6: [amv, alive, sh, chg, amt]}}`。
  取不到 / 超时 / 异常 → `None`（调用方回退收盘口径，**不编造**），原因走 `last_error()`。
"""
from __future__ import annotations

import contextlib
import importlib
import sys
import threading
import time
from typing import Any, Optional

from app.core import paths

# 旧端常量（`engine/server.py:2534-2537`）；JOIN 上限按任务书取 6s。
TTL_IN_SESSION = 25.0        # 盘中：25s
TTL_OFF_SESSION = 300.0      # 盘外（周末/收盘后）：300s，只算很少几次，不白烧 QMT
JOIN_TIMEOUT_S = 6.0         # 等快照上限（典型 0.5s；周末 QMT 未开约 4.2s 返回 None）
INFLIGHT_MAX_S = 60.0        # 单次计算最长占用上限：超过判卡死，允许下一拍重起（正常 4–6s）
SESSION_PROBE_TIMEOUT_S = 2.0  # 交易日历探测上限（eltdx 无缓存时可能慢，超时按工作日兜底）
SESSION_TTL_S = 20.0         # 时段判定缓存，避免每请求都探日历
SESSION_FALLBACK_START = "09:30"   # 日历不可用时的兜底窗口（任务书指定）
SESSION_FALLBACK_END = "15:00"

# 懒加载目标（测试可替换；生产恒为旧仓 `engine/executor/amv_live.py`）
IMPORT_PACKAGE = "executor"
IMPORT_MODULE = "amv_live"

_LOCK = threading.Lock()                  # 只护 _STATE 字段读写（不再当计算锁）
_IMPORT_LOCK = threading.Lock()           # sys.path 改动串行化（只护 import，不护整个计算）
_STATE: dict[str, Any] = {"ts": 0.0, "doc": None, "err": None,
                            "inflight": False, "inflight_at": 0.0, "gen": 0}
_SESSION: dict[str, Any] = {"ts": 0.0, "value": False}


def _engine_dir() -> Any:
    """旧仓 import 根：`<WS>/engine`（`executor` / `common` 两个包都在它下面）。"""
    return paths.WS / "engine"


@contextlib.contextmanager
def _engine_on_path():
    """临时把 `<WS>/engine` 插进 `sys.path`，退出时**只删本次新增的条目**。

    为什么不整体还原快照（`contract.lib()` 的做法）：本模块的导入发生在工作线程里，而
    `join()` 超时后主线程可以先去干别的事（包括自己插 `sys.path`），工作线程稍后无条件
    `sys.path[:] = 快照` 会把那些新条目一起抹掉。这里改成差集删除：本轮新出现的目录一律清掉
    （含旧仓模块 import 期自己插的），其它条目不动。
    """
    engine_dir = str(_engine_dir())
    with _IMPORT_LOCK:
        path_before = list(sys.path)
        if engine_dir not in sys.path:
            sys.path.insert(0, engine_dir)
        try:
            yield
        finally:
            added = [p for p in sys.path if p not in path_before]
            if added:
                sys.path[:] = [p for p in sys.path if p not in added]


def _import_target() -> Any:
    """懒加载旧仓 `executor.amv_live`，**导入后立即还原 sys.path**。

    返回模块对象；import 失败原样抛出（由 `snapshot()` 的 worker 统一装进错误文本）。

    为什么要临时插 path：`executor` 是 `engine/` 下的包，`amv_live` 顶层还 `from common.paths
    import ...`，两者都要求 `engine/` 在 `sys.path` 上。插件进程不常驻这一条（不像旧
    server.py 在 import 期就插），只能按需插、用完还原。

    还原放在 import 之后（不是整个调用之后）：`stocks_snapshot()` 本身要跑几秒，期间不该
    占着 path 修改也不该等它跑完——否则 `join()` 超时后，工作线程的清理会被拖到几秒之后。
    """
    with _engine_on_path():
        return importlib.import_module("%s.%s" % (IMPORT_PACKAGE, IMPORT_MODULE))


def _fallback_in_session() -> bool:
    """日历不可用时的兜底：工作日且 09:30 ≤ HH:MM < 15:00（任务书指定口径）。"""
    now = time.localtime()
    if now.tm_wday >= 5:
        return False
    hhmm = time.strftime("%H:%M", now)
    return SESSION_FALLBACK_START <= hhmm < SESSION_FALLBACK_END


def _probe_in_session() -> bool:
    """问一次 `common.trading_calendar.in_trading_session()`；不可用/超时 → 工作日兜底。

    单独开线程是为了给日历探测设上限：eltdx 无缓存时 `is_trading_day` 现拉要几百毫秒到数秒，
    而这条路径挂在请求线程上（每个 `live=1` 请求都会问一次）。超时后线程继续跑完（daemon），
    这里按兜底口径先给出答案，不阻塞面板。

    **import 与调用分开**：import 在锁内完成并立即还原 path（见 `_engine_on_path`），
    慢的日历查询在锁外执行——否则 `join()` 超时后工作线程还握着 `_IMPORT_LOCK` 等到查完。
    """
    box: dict[str, Any] = {}

    def _job() -> None:
        try:
            with _engine_on_path():
                from common.trading_calendar import in_trading_session  # noqa: PLC0415
            box["value"] = bool(in_trading_session())
        except Exception as exc:  # noqa: BLE001 - 日历不可用不拖垮面板
            box["error"] = exc

    worker = threading.Thread(target=_job, name="panel-v2-amv-live-session", daemon=True)
    worker.start()
    worker.join(SESSION_PROBE_TIMEOUT_S)
    if worker.is_alive() or "value" not in box:
        return _fallback_in_session()
    return bool(box["value"])


def in_session() -> bool:
    """当前是否交易时段（20s 缓存；日历不可用 → 工作日 09:30–15:00 兜底）。

    不可用一律走兜底、不抛：时段只决定缓存长短（25s vs 300s），猜错**不影响口径正确性**，
    只影响 QMT 调用频率。所以这里宁可兜底也不把日历故障传给面板。
    """
    now = time.time()
    if (now - float(_SESSION["ts"])) < SESSION_TTL_S:
        return bool(_SESSION["value"])
    try:
        value = _probe_in_session()
    except Exception:  # noqa: BLE001 - 探测整体失败 → 兜底（不当成盘外，也不能炸）
        value = _fallback_in_session()
    _SESSION["ts"] = now
    _SESSION["value"] = value
    return value


def _valid_snapshot(doc: Any) -> bool:
    """形状校验：旧仓返回 `{date, asof, all_amv, stocks{...}}`，缺项当坏数据（不猜）。"""
    return (
        isinstance(doc, dict)
        and isinstance(doc.get("date"), str) and bool(doc.get("date"))
        and isinstance(doc.get("stocks"), dict)
        and isinstance(doc.get("all_amv"), (int, float)) and not isinstance(doc.get("all_amv"), bool)
    )


def snapshot() -> Optional[dict]:
    """实时逐股快照（缓存：盘中 25s / 盘外 300s）。

    返回旧仓 `stocks_snapshot()` 原样 dict；取不到（数据源未开 / 超时 / 异常 / 空快照）→ `None`。
    **失败也写缓存**：防连点把数据源打穿（旧端同款）。原因用 `last_error()` 查。

    超时语义（2026-11-16 修）：`join` 到点只代表“本轮没等到”，后台照旧跑完并写缓存；
    已有的实时帧**不清空**，返回上一帧 + `last_error()=="timeout"`，下一拍直接命中。
    """
    now = time.time()
    ttl = TTL_IN_SESSION if in_session() else TTL_OFF_SESSION
    with _LOCK:
        if (now - float(_STATE["ts"])) < ttl:
            return _STATE["doc"]
        # 上一轮还在算 → 用旧帧，不排队、**不叠加**新 worker（旧端同款）。
        # 2026-11-16 修：以前用 `_LOCK.acquire(blocking=False)` 表达这条语义，但 join 超时后
        # finally 立即还锁，而后台 worker 还在跑 → 下一拍又起一个，盘初数据源变慢时叠出 N 个
        # 并发全量快照，彼此抢 CPU/连接，越算越慢、个个超时（主人报“开盘近半小时实时不可用”）。
        if _STATE["inflight"] and (now - float(_STATE["inflight_at"])) < INFLIGHT_MAX_S:
            return _STATE["doc"]
        _STATE["inflight"] = True
        _STATE["inflight_at"] = now
        gen = int(_STATE["gen"])

    def _job() -> None:
        """算一次全市场快照，**不管请求线程还在不在等，跑完都把结果写进缓存**。

        2026-11-16 修：以前结果只活在 `box` 里，请求线程 join 超时后就丢掉——后台白算一场，
        下一次请求从零重来，永远热不起来；而且超时分支会把 `_STATE["doc"]` 覆盖成 None，
        连上一次成功的实时帧一起清掉，前端立刻掉回收盘口径且长时间爬不回来。
        """
        doc = None
        err = None
        try:
            module = _import_target()
            doc = module.stocks_snapshot()
        except Exception as exc:  # noqa: BLE001 - 不拖垮面板
            err = "%s: %s" % (type(exc).__name__, exc)
        if doc is not None and not _valid_snapshot(doc):
            doc, err = None, (err or "bad_snapshot")
        if doc is None and err is None:
            err = "empty"
        with _LOCK:
            if _STATE["gen"] != gen:
                return        # 缓存已被重置（测试隔离 / 进程重载）→ 本轮结果作废，不覆盖新代
            _STATE["doc"] = doc
            _STATE["err"] = err
            _STATE["ts"] = time.time()
            _STATE["inflight"] = False

    worker = threading.Thread(target=_job, name="panel-v2-amv-live", daemon=True)
    worker.start()
    worker.join(JOIN_TIMEOUT_S)
    if worker.is_alive():
        # 到点不 kill：后台继续跑完并自己写缓存；本轮只报原因，**不动已有实时帧、不刷 ts**
        # （刷 ts 会把“正在算”错记成“刚失败”，多占 25s TTL；清 doc 会让面板从实时掉回收盘）。
        # 锁内再确认一次 inflight：worker 恰好已跑完时不覆盖它刚写好的真实原因。
        with _LOCK:
            if _STATE["gen"] == gen and _STATE["inflight"]:
                _STATE["err"] = "timeout"
    return _STATE["doc"]


def last_error() -> Optional[str]:
    """最近一次取快照的失败原因（`timeout` / `<ExcType>: <msg>` / `empty` / `bad_snapshot`）。

    成功（有文档）时为 `None`。上层拿它填 `live_err`，**不把失败包装成正常数据**。
    """
    err = _STATE.get("err")
    return str(err) if err else None


def cached_at() -> float:
    """最近一次写入缓存的时间戳（诊断/测试用）。"""
    return float(_STATE["ts"])


def reset_cache() -> None:
    """清缓存（测试隔离用；生产不需要调）。

    同时自增代际号：已经在飞的后台 worker 跑完后不再回写（否则会把上一条用例的
    快照涂到下一条用例的缓存里，测试顺序一变就红）。
    """
    with _LOCK:
        _STATE["ts"] = 0.0
        _STATE["doc"] = None
        _STATE["err"] = None
        _STATE["inflight"] = False
        _STATE["inflight_at"] = 0.0
        _STATE["gen"] = int(_STATE["gen"]) + 1
    _SESSION["ts"] = 0.0
    _SESSION["value"] = False
