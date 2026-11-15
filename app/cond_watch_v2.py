# -*- coding: utf-8 -*-
"""cond_watch_v2.py · 「面板关了也能推」的条件单评估独立进程（2026-10-28）

## 为什么有这个进程

调研 `docs/RESEARCH_20261028_cond_push.md` §2.4 / §6 R2 确认：面板 v2 的条件单评估挂在
8810 的 feed 派发线程上（`app/core/paper/service.py::_on_quotes`）。面板一关，feed 停，
条件单就没人评估——止损单在盘中形同不存在。主人 2026-10-28 拍板：独立常驻进程接管。

## 评估权（同一时刻只有一个评估者）

- **本进程是默认唯一评估者**：活着时每轮写心跳 `data/panel_v2/cond_watch_heartbeat.json`，
  面板 `_on_quotes` 见到新鲜心跳（<30s 且 ok）就跳过 `evaluate` / `match_pending`；
- 本进程**不因面板在而让位**（主人要的是"面板关了也推"，让位就白做）。启动时只探测一次
  面板在不在，写进日志，不影响评估权；
- 本进程死掉/卡住 → 心跳过期 → 面板自动兜底（这是可用性兜底，不是双评估）；
- 心跳 `ok=False`（本轮异常 / 优雅退出）同样视为"不在场"，面板立刻接管。

## 与旧 `engine/executor/cond_watch.py` 的差别

| | 旧 cond_watch | 本进程 |
|---|---|---|
| 账本 | `engine/state/conditional_orders.json` | `data/panel_v2/paper/`（`PANEL_V2_PAPER_DIR` 可覆盖） |
| 行情 | `server._order_quote`（eltdx 五档 + ATR14/MA5） | `app/core/quotes.order_quote`（同源同口径） |
| 推送 | 无 | `app/core/notify.enqueue`（QQ 官方 bot，异步 + 300s 去重） |
| 盘外 | 自退，等 watchdog 拉起 | **常驻**，sleep 60s 等下一轮（主人要的是常驻） |

## 边界

- 状态机不在本文件：一律调 `PaperOrderService.evaluate()` / `match_pending()`，
  条件类型与触发语义的唯一源是 `app/core/paper/orders.py`；
- 不改账本格式、不碰 notify / orders / 其它插件；
- 单轮异常写 audit 后继续，**不退出**（行情抖动不能杀死监控）；
- 行情不新建 TdxClient：只走 `app/core/quotes.py` 的进程内单例 + TTL 缓存。

## 用法

    python app/cond_watch_v2.py                 # 常驻（5s/轮；盘外 60s/轮）
    python app/cond_watch_v2.py --once          # 单轮自检后退出（**不写心跳**，不影响面板）
    python app/cond_watch_v2.py --interval 3    # 改盘中轮询间隔
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
import urllib.request
from datetime import datetime, time as _dtime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

# 允许 `python app/cond_watch_v2.py` 直接跑（不依赖已安装的包/工作目录）
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.core import paths  # noqa: E402
from app.core import quotes as quotes_mod  # noqa: E402
from app.core.notify import enqueue  # noqa: E402 - 契约要求：命中推送走 enqueue（永不抛）
from app.core.paper import service as paper_service  # noqa: E402
from app.core.paper import state  # noqa: E402
from app.core.paper.orders import COND_TYPES  # noqa: E402

logger = logging.getLogger("panel_v2.cond_watch")

# ---------------------------------------------------------------- 常量（口径）
# 盘中轮询间隔（与旧 cond_watch / 旧 `_order_watch_loop` 一致）
POLL_INTERVAL_S = 5
# 盘外轮询间隔（主人要求常驻；不追行情，只等开盘）
IDLE_INTERVAL_S = 60
# 心跳判活窗口：**必须与 app/core/paper/service.py 的 _COND_WATCH_MAX_AGE_S 一致**
HEARTBEAT_MAX_AGE_S = 30.0
# 心跳文件（面板让位判据；契约 §1）
HEARTBEAT_FILE: Path = paths.OUT_DIR / "cond_watch_heartbeat.json"
# 面板心跳（可选；仅启动日志用）
PANEL_HEARTBEAT_FILE: Path = paths.OUT_DIR / "panel_heartbeat.json"
PANEL_HEALTH_URL = "http://127.0.0.1:8810/api/health"
PANEL_PROBE_TIMEOUT_S = 1.0

# 交易时段（连续竞价）：09:30–11:30 / 13:00–15:00
_MORNING = (_dtime(9, 30), _dtime(11, 30))
_AFTERNOON = (_dtime(13, 0), _dtime(15, 0))

_STOP = threading.Event()


# ---------------------------------------------------------------- 心跳


def write_heartbeat(ok: bool = True, round_ms: int = 0, *, rounds: int = 0,
                    skipped: Optional[str] = None, last_error: Optional[str] = None,
                    state_name: str = "running", path: Optional[Any] = None) -> bool:
    """原子写心跳（tmp + os.replace）。写失败返回 False，**绝不抛**。

    字段：`{ts, pid, ok, round_ms}`（契约 §1）+ `rounds/skipped/last_error/state/updated_at`
    供人排查。`ts` 是 epoch 秒（面板拿它算新鲜度）。
    """
    target = Path(path or HEARTBEAT_FILE)
    doc = {
        "ts": round(time.time(), 3),
        "pid": os.getpid(),
        "ok": bool(ok),
        "round_ms": int(round_ms or 0),
        "rounds": int(rounds or 0),
        "skipped": skipped,
        "last_error": (str(last_error)[:300] if last_error else None),
        "state": state_name,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    tmp = target.parent / ("%s.%d.tmp" % (target.name, os.getpid()))
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        for _ in range(3):
            try:
                os.replace(tmp, target)
                return True
            except OSError:      # Windows 上读方短窗口占用 → 重试
                time.sleep(0.02)
        return False
    except OSError:
        return False
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def read_heartbeat(path: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    """读心跳；缺失/损坏/正被替换 → None。"""
    try:
        raw = json.loads(Path(path or HEARTBEAT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def heartbeat_fresh(hb: Optional[Dict[str, Any]], now: Optional[float] = None,
                    max_age: float = HEARTBEAT_MAX_AGE_S) -> bool:
    """心跳是否「在场」：ok=True 且 0 ≤ now-ts < max_age。"""
    if not isinstance(hb, dict) or not hb.get("ok"):
        return False
    try:
        ts = float(hb.get("ts") or 0)
    except (TypeError, ValueError):
        return False
    age = (time.time() if now is None else float(now)) - ts
    return 0.0 <= age < float(max_age)


# ---------------------------------------------------------------- 交易日 / 时段


def is_trading_day(day: Any) -> bool:
    """交易日判定：优先 `common.trading_calendar`（eltdx 全历史日历），不可用 → 工作日兜底。

    兜底只影响"多判几个节假日"，方向是**宁可少评估**（旧 cond_watch 同口径）。
    """
    engine_dir = str(paths.WS / "engine")
    inserted = engine_dir not in sys.path
    if inserted:
        sys.path.append(engine_dir)
    try:
        from common.trading_calendar import is_trading_day as _cal  # noqa: PLC0415
        return bool(_cal(day))
    except Exception:  # noqa: BLE001 - 日历不可用 → 工作日兜底
        return getattr(day, "weekday", lambda: 7)() < 5
    finally:
        if inserted:
            sys.path[:] = [p for p in sys.path if p != engine_dir]


def in_session(now: Optional[datetime] = None) -> bool:
    """是否「交易日 + 连续竞价时段」（本进程唯一的评估闸门）。"""
    n = now or datetime.now()
    if n.weekday() >= 5:
        return False
    if not is_trading_day(n.date()):
        return False
    t = n.time()
    return (_MORNING[0] <= t <= _MORNING[1]) or (_AFTERNOON[0] <= t <= _AFTERNOON[1])


# ---------------------------------------------------------------- 面板探测（仅日志）


def panel_online(timeout: float = PANEL_PROBE_TIMEOUT_S) -> bool:
    """面板是否在：优先面板心跳文件，否则探一次 `127.0.0.1:8810/api/health`。

    只用于启动日志说明「面板在不在」，**不参与评估权判定**（本进程不让位）。
    """
    if heartbeat_fresh(read_heartbeat(PANEL_HEARTBEAT_FILE)):
        return True
    try:
        with urllib.request.urlopen(PANEL_HEALTH_URL, timeout=timeout) as resp:
            return 200 <= int(getattr(resp, "status", 0)) < 300
    except Exception:  # noqa: BLE001 - 探测失败 = 面板不在（不影响本进程）
        return False


# ---------------------------------------------------------------- 单轮评估


def _today8() -> str:
    return time.strftime("%Y%m%d")


def _fmt_px(v: Any) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "-"
    return "%.2f" % f if f == f else "-"


def _pending_codes(svc) -> list[str]:
    """需要补撮合的挂单代码（REPORTED/PARTIAL，来源 manual/conditional；同旧 cond_watch 口径）。"""
    out = []
    for r in svc.list_orders() or []:
        if not isinstance(r, dict):
            continue
        if str(r.get("status")) not in ("REPORTED", "PARTIAL"):
            continue
        if str(r.get("source")) not in ("manual", "conditional"):
            continue
        code = r.get("ts_code") or r.get("code")
        if code:
            out.append(str(code))
    return out


def push_triggers(triggered: list[Dict[str, Any]]) -> int:
    """把命中条件单推给 notify（永不抛）。返回成功入队条数。

    dedup_key = `{cond_id}:{date}:{price}`（调研 §6 R1：OCO/重启重放会重复命中）。
    """
    sent = 0
    for c in triggered or []:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("cond_id") or "")
        code = str(c.get("code") or "")
        name = str(c.get("name") or "").strip()
        spec = COND_TYPES.get(str(c.get("type"))) or {}
        label = str(spec.get("label") or c.get("type") or "条件单")
        trig = c.get("trigger") or {}
        trig_px = c.get("trigger_price") or trig.get("price")
        last = c.get("trigger_price")
        status = str(c.get("status") or "")
        side = str(c.get("side") or spec.get("side") or "").upper()
        body = "\n".join([
            "条件单 %s · %s" % (cid, label),
            "触发价 %s / 当时价 %s" % (_fmt_px(trig.get("price")), _fmt_px(last)),
            "%s %s 股 · 委托 %s" % ("卖出" if side == "SELL" else "买入",
                                    int(c.get("triggered_qty") or c.get("qty") or 0),
                                    c.get("order_id") or "-"),
        ])
        if status and status != "TRIGGERED":
            body += "\n状态 %s：%s" % (status, str(c.get("reason") or "")[:120])
        title = "条件单触发 · %s %s" % (name or code, code)
        try:
            res = enqueue("COND_TRIGGERED", title, body,
                          dedup_key="%s:%s:%s" % (cid, _today8(), _fmt_px(last)))
        except Exception as exc:  # noqa: BLE001 - 推送失败不影响账本状态（契约 §4）
            logger.warning("推送入队异常（已忽略）：%s", exc)
            continue
        if isinstance(res, dict) and res.get("ok"):
            sent += 1
        else:
            logger.info("推送未入队：%s（%s）", cid, (res or {}).get("reason"))
    return sent


# ---------------------------------------------------------------- 均线/关键K（2026-10-28）
#
# 回踩均线 / 关键K 两类条件单需要 yellow / white / ek_open / ek_close。
# 公式**不在这里重写**：直接调 buy_module 正本（作战表关键位同一套 select_levels 的上游）：
#   黄线=(MA14+MA28+MA57+MA114)/4、白线=EMA(EMA(C,10),10) → signals.common.zg_ma_lines
#   动能K=放量阳线（base>20 且 VOL>REF(VOL,1)×1.5） → signals.common.calc_momentum_ks（取最近一根未破的）
# buy_module 加载失败时这几个字段留空，两类条件单评估时自然跳过（不猜、不补零）。
_BUY_FNS: Dict[str, Any] = {}


def _buy_fns():
    if _BUY_FNS:
        return _BUY_FNS
    buy = str(paths.WS / "buy_module")
    if buy not in sys.path:
        sys.path.insert(0, buy)
    from signals.common import calc_momentum_ks, zg_ma_lines  # noqa: PLC0415
    _BUY_FNS["calc_momentum_ks"] = calc_momentum_ks
    _BUY_FNS["zg_ma_lines"] = zg_ma_lines
    return _BUY_FNS


def _daily_rows(code: str, count: int = 200):
    """日线（前复权）→ buy_module 要的 dict 列表；取不到返回 []。"""
    glb = quotes_mod.normalize(code)
    if not glb:
        return []
    rows = quotes_mod.daily_bars(glb, count, adjust="qfq") or []
    out = []
    for b in rows:
        try:
            t = getattr(b, "time", None)
            out.append({
                "date": t.strftime("%Y%m%d") if hasattr(t, "strftime") else "",
                "open": float(getattr(b, "open")), "high": float(getattr(b, "high")),
                "low": float(getattr(b, "low")), "close": float(getattr(b, "close")),
                "volume": float(getattr(b, "volume_lots", 0) or 0),
            })
        except (TypeError, ValueError):
            continue
    return out


def enrich_levels(code: str, q: Dict[str, Any]) -> Dict[str, Any]:
    """给行情快照补 yellow/white/ek_open/ek_close（失败保持原样，不抛）。"""
    if not isinstance(q, dict) or not q:
        return q
    if all(k in q for k in ("yellow", "white", "ek_open", "ek_close")):
        return q
    try:
        rows = _daily_rows(code)
        if len(rows) >= 15:
            fns = _buy_fns()
            wy = fns["zg_ma_lines"]([r["close"] for r in rows]) or {}
            if wy.get("yellow") is not None:
                q["yellow"] = float(wy["yellow"])
            if wy.get("white") is not None:
                q["white"] = float(wy["white"])
            for mk in (fns["calc_momentum_ks"](rows, 20, 3) or []):
                if mk.get("broke_lower"):
                    continue
                q["ek_open"] = float(mk["open"])
                q["ek_close"] = float(mk["close"])
                break
    except Exception:  # noqa: BLE001 - 指标缺失不阻断评估
        pass
    return q


def evaluate_once(*, svc=None, now: Optional[datetime] = None,
                  quote_fn: Optional[Callable[[str], Dict[str, Any]]] = None) -> Dict[str, Any]:
    """一轮评估。返回 stats（`--once` 自检与心跳都消费它）。

    - 盘外直接返回 `skipped="closed"`，**不取行情、不评估**（夜里用昨天价触发 = 假成交）；
    - 单票取价失败跳过该票，不影响其它票；
    - 评估/撮合异常记 `error`，由调用方写 audit 后继续下一轮。
    """
    now = now or datetime.now()
    st: Dict[str, Any] = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "skipped": None,
                          "conditions": 0, "pending": 0, "codes": 0, "quoted": 0,
                          "triggered": 0, "pushed": 0, "error": None}
    if not in_session(now):
        st["skipped"] = "closed"
        return st

    try:
        svc = svc or get_service()
        conds = svc.list_conditions(status="ACTIVE") or []
        pend_codes = _pending_codes(svc)
        codes = {str(c.get("code")) for c in conds if isinstance(c, dict) and c.get("code")}
        codes.update(pend_codes)
        st["conditions"] = len(conds)
        st["pending"] = len(pend_codes)
        st["codes"] = len(codes)
        if not codes:
            return st

        get_quote = quote_fn or quotes_mod.order_quote
        quotes: Dict[str, Dict[str, Any]] = {}
        for code in sorted(codes):
            try:
                q = get_quote(code)
                q = enrich_levels(code, q)
            except Exception:  # noqa: BLE001 - 单票失败不影响其它票
                continue
            if isinstance(q, dict) and q:
                quotes[code] = q
        st["quoted"] = len(quotes)
        if not quotes:
            return st

        res = svc.evaluate(quotes) or {}
        triggered = [c for c in (res.get("triggered") or []) if isinstance(c, dict)]
        st["triggered"] = len(triggered)
        # 先推再撮合：place() 已在 evaluate 内完成，推送不依赖撮合结果（契约 §4）
        st["pushed"] = push_triggers(triggered)
        svc.match_pending(quotes)
    except Exception as exc:  # noqa: BLE001 - 单轮异常不退出（契约 §5）
        st["error"] = "%s: %s" % (type(exc).__name__, exc)
    return st


_SVC = {"svc": None}


def get_service():
    """进程内单例（PaperBroker 单号地板只扫一次盘；与面板同一个装配入口）。"""
    if _SVC["svc"] is None:
        _SVC["svc"] = paper_service.build_service()
    return _SVC["svc"]


def reset_service() -> None:
    """测试用：丢弃单例（不删任何文件）。"""
    _SVC["svc"] = None


# ---------------------------------------------------------------- 单实例


def _pid_alive(pid: Any) -> bool:
    """PID 是否存活（Windows GetExitCodeProcess == STILL_ACTIVE）。非 Windows → 保守 False。"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        import ctypes  # noqa: PLC0415

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return bool(ok) and code.value == 259      # STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001 - 非 Windows / 权限不足：保守判不存活
        return False


def single_instance_guard(path: Optional[Any] = None) -> None:
    """重复实例 = 重复评估 = 重复下单（2026-09-19 旧链事故教训）。有活实例就退出。"""
    hb = read_heartbeat(path)
    old = (hb or {}).get("pid")
    if heartbeat_fresh(hb) and int(old or 0) != os.getpid() and _pid_alive(old):
        raise SystemExit("[cond_watch_v2] 已有实例在跑（pid=%s，心跳 %s），本进程退出"
                         % (old, (hb or {}).get("updated_at")))


# ---------------------------------------------------------------- 主循环


def _audit(event: str, detail: str) -> None:
    """写账本 audit（失败静默）。"""
    try:
        state.audit(event, detail)
    except Exception:  # noqa: BLE001
        pass


def _install_signals() -> None:
    """SIGINT/SIGTERM → 置停止位（优雅退出，退出时写 ok=false 心跳）。"""
    def _handler(signum, _frame):  # noqa: ANN001 - signal handler 签名固定
        logger.info("收到信号 %s，准备退出", signum)
        _STOP.set()

    for name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _handler)
        except (OSError, ValueError):   # 非主线程/平台不支持
            pass


def run_forever(interval: int = POLL_INTERVAL_S, *, svc=None,
                quote_fn: Optional[Callable[[str], Dict[str, Any]]] = None,
                stop: Optional[threading.Event] = None,
                heartbeat_path: Optional[Any] = None) -> int:
    """常驻主循环：盘内 5s/轮，盘外 60s/轮；每轮写一次心跳。"""
    stop = stop or _STOP
    rounds = 0
    last_err: Optional[str] = None
    logger.info("启动：盘中 %ss/轮、盘外 %ss/轮；交易日 09:30–11:30 / 13:00–15:00 评估",
                max(1, int(interval)), IDLE_INTERVAL_S)
    logger.info("面板在线：%s（不影响本进程评估权）", panel_online())
    # 先写一次心跳：面板立刻让位，避免启动瞬间两边各评一次
    write_heartbeat(True, 0, rounds=0, state_name="starting", path=heartbeat_path)
    while not stop.is_set():
        # 轮首也写一次心跳：单轮取数慢（多标的 × eltdx 超时）时也不会让面板误判"无人评估"
        write_heartbeat(True, 0, rounds=rounds, state_name="round", path=heartbeat_path)
        t0 = time.monotonic()
        st: Dict[str, Any] = {}
        try:
            st = evaluate_once(svc=svc, quote_fn=quote_fn)
        except Exception as exc:  # noqa: BLE001 - 兜底：evaluate_once 之外的意外也不退出
            st = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "error": "%s: %s" % (type(exc).__name__, exc)}
        ms = int(round((time.monotonic() - t0) * 1000))
        rounds += 1
        err = st.get("error")
        last_err = err or last_err
        if err:
            _audit("cond_watch.round_error", err)
            logger.warning("第 %d 轮异常（下轮重试）：%s", rounds, err)
        write_heartbeat(not err, ms, rounds=rounds, skipped=st.get("skipped"),
                        last_error=err, path=heartbeat_path)
        if st.get("triggered"):
            _audit("cond_watch.triggered",
                   "rounds=%d triggered=%d pushed=%d" % (rounds, st["triggered"], st.get("pushed", 0)))
        stop.wait(IDLE_INTERVAL_S if st.get("skipped") else max(1, int(interval)))
    # 优雅退出：ok=False 让面板立刻接管（保留末轮错误，便于排查为何退出）
    write_heartbeat(False, 0, rounds=rounds, state_name="stopped",
                    last_error=last_err or "graceful_stop", path=heartbeat_path)
    _audit("cond_watch.exit", "rounds=%d graceful_stop" % rounds)
    logger.info("已停止（共 %d 轮）；心跳已置 ok=false，面板接管", rounds)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="cond_watch_v2",
        description="面板 v2 条件单评估独立进程（面板关了也能推；盘中 5s/轮）")
    ap.add_argument("--once", action="store_true", help="单轮评估后退出（自检用，不写心跳）")
    ap.add_argument("--interval", type=int, default=POLL_INTERVAL_S,
                    help="盘中轮询间隔秒（默认 %d）" % POLL_INTERVAL_S)
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    if args.once:
        st = evaluate_once()
        print("[cond_watch_v2] 单轮完成：" + json.dumps(st, ensure_ascii=False))
        return 0 if not st.get("error") else 1

    single_instance_guard()
    _install_signals()
    logger.info("账本目录：%s", state.get_paths().root)
    logger.info("心跳文件：%s", HEARTBEAT_FILE)
    return run_forever(interval=args.interval)


if __name__ == "__main__":
    sys.exit(main())
