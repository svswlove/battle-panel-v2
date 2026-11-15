# -*- coding: utf-8 -*-
"""app/core/paper/state.py · 纸面账本的状态层（路径 / 锁 / 原子写 / 备份 / 审计）

【职责】纸面交易**唯一允许写文件的地方**。broker.py / orders.py / service.py 一律
调用本模块的 API，自己不得出现 `open(..., "w")` / `os.replace` / `os.remove` /
`Path.write_text` —— 这是任务书 4.1.2 硬约束第 3 条，验收会静态扫。

【为什么单独一层】
旧链（engine/core/paper_broker.py + manual_orders.py）把「读写 JSON + tmp 替换 +
文件锁 + 备份」散在十几个方法里，多进程并发下单出过两类事故：
  ① `os.replace` 在 Windows 上撞读方短窗口 PermissionError → 直接抛，单子丢失；
  ② 账本没有备份，写坏一次就无从回退。
本层把这两件事收成两句话：所有写都走 `atomic_write_json()`，所有读都走 `read_json()`。

【路径隔离（硬约束第 4 条）】
账本目录默认 `<panel-v2>/data/panel_v2/paper/`（见 paths.PAPER_STATE_DIR），
**绝不碰** `engine/state/*`（旧 8800 的 cond-push 线程还在写）与 `data/panel/*`
（生产 panel JSON 只读白名单）。`PANEL_V2_PAPER_DIR` 环境变量可覆盖，
且**每次 get_paths() 都重读**——测试在 import 之后再设 env 也生效，绝不写真实账本目录。

【备份节流】
`atomic_write_json()` 写前调 `backup_file()`，但**同一进程同一路径 60 秒内只备份一次**
（旧实现若每笔成交都备份，一开盘几百单就把磁盘打爆）。真实回退需求由
`backups/startup_*` 与 `reset_to_initial()` 的归档目录兜底。
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from app.core import paths as _paths
from app.core.paper.filelock import FileLockTimeout, exclusive_lock  # noqa: F401 - 对外复用

__all__ = [
    "FileLockTimeout",
    "PaperPaths",
    "atomic_write_json",
    "audit",
    "backup_file",
    "get_paths",
    "locked",
    "now_str",
    "read_json",
    "reset_to_initial",
]

# 账本文件名（唯一源：broker / orders / service 都从这里取，不各自拼字符串）
ACCOUNT_NAME = "paper_account.json"
TRADES_NAME = "paper_trades.json"
CONDITIONS_NAME = "conditional_orders.json"
FREEZE_NAME = "manual_orders_state.json"
MANUAL_LOCK_NAME = "manual_lock.json"
ENGINE_STATE_NAME = "engine_state.json"
KILL_SWITCH_NAME = "kill_switch.json"
AUDIT_NAME = "audit.log"
BACKUPS_NAME = "backups"

# 纸面初始资金（元；2026-08-24 主人定 50 万）
INITIAL_CASH = 500000.0

# 备份节流窗口：同一进程同一路径 60s 内只备份一次（防高频下单把磁盘打爆）
BACKUP_THROTTLE_S = 60.0
# 每个「文件+标签」保留的备份份数上限（超出的从旧到新删）
BACKUP_KEEP = 200


@dataclass(frozen=True)
class PaperPaths:
    """一份账本目录的全部落地路径（frozen：装配后不再改，避免半路换目录写错账）。"""

    root: Path
    account: Path        # paper_account.json（独立账本，每笔成交原子更新）
    trades: Path         # paper_trades.json（委托/成交流水）
    conditions: Path     # conditional_orders.json（条件单）
    freeze: Path         # manual_orders_state.json（挂单冻结的资金/持仓）
    manual_lock: Path    # manual_lock.json（手工优先锁：{code: {date, ts, order_id}}）
    engine_state: Path   # engine_state.json（只读；trade_mode 固定 paper）
    kill_switch: Path    # kill_switch.json（纸面急停；缺省不存在 = 未急停）
    audit: Path          # audit.log（每行一条 JSON：ts/event/detail）
    backups: Path        # backups/（写前快照 + reset 归档）


def _paper_root(root: Optional[Any] = None) -> Path:
    """解析账本根目录：显式 root 优先 → 环境变量 PANEL_V2_PAPER_DIR → paths.PAPER_STATE_DIR。

    环境变量在这里**每次重读**（不走 import 期常量），测试用 tmp 目录隔离时不写真实账本。
    """
    if root is not None:
        return Path(root).expanduser()
    env = os.environ.get("PANEL_V2_PAPER_DIR")
    if env:
        return Path(env).expanduser()
    return Path(_paths.PAPER_STATE_DIR)


def get_paths(root: Optional[Any] = None) -> PaperPaths:
    """账本目录 → PaperPaths（纯计算，不建目录、不写盘）。"""
    r = _paper_root(root)
    return PaperPaths(
        root=r,
        account=r / ACCOUNT_NAME,
        trades=r / TRADES_NAME,
        conditions=r / CONDITIONS_NAME,
        freeze=r / FREEZE_NAME,
        manual_lock=r / MANUAL_LOCK_NAME,
        engine_state=r / ENGINE_STATE_NAME,
        kill_switch=r / KILL_SWITCH_NAME,
        audit=r / AUDIT_NAME,
        backups=r / BACKUPS_NAME,
    )


def now_str(ts: Optional[float] = None) -> str:
    """统一时间口径（YYYY-MM-DD HH:MM:SS，本地时区）。"""
    return time.strftime("%Y-%m-%d %H:%M:%S",
                         time.localtime(time.time() if ts is None else ts))


def read_json(path: Any, default: Any = None) -> Any:
    """宽容读 JSON：文件缺失/损坏/权限异常 → default（账本读失败不得打断主流程）。"""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


# ------------------------------------------------------------------ 审计
_AUDIT_LOCK = threading.Lock()


def audit(event: str, detail: str = "", root: Optional[Any] = None) -> None:
    """追加一条写审计到 <root>/audit.log（每行 JSON：ts / event / detail）。

    失败**静默**：审计是旁路，绝不允许因为磁盘满 / 目录只读把下单主链打断。
    root 缺省 = 默认账本目录；atomic_write_json 内部传目标文件的父目录，
    这样测试用 tmp 目录时审计也落在 tmp 里，不污染真实账本目录。
    """
    try:
        folder = Path(root) if root is not None else get_paths().root
        line = json.dumps({"ts": now_str(), "event": str(event or ""),
                           "detail": str(detail or "")}, ensure_ascii=False)
        folder.mkdir(parents=True, exist_ok=True)
        with _AUDIT_LOCK:
            with open(folder / AUDIT_NAME, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:  # noqa: BLE001 - 审计失败一律吞掉（旁路不得影响账本写入）
        pass


# ------------------------------------------------------------------ 备份
_BACKUP_TS: Dict[str, float] = {}
_BACKUP_LOCK = threading.Lock()


def _prune_backups(folder: Path, prefix: str) -> None:
    """保留最近 BACKUP_KEEP 份同前缀备份（从旧到新删）。失败静默。"""
    try:
        cand = sorted(folder.glob(prefix + "*"), key=lambda p: p.name)
        if len(cand) > BACKUP_KEEP:
            for old in cand[:-BACKUP_KEEP]:
                try:
                    os.remove(old)
                except OSError:
                    pass
    except OSError:
        pass


def backup_file(path: Any, tag: str = "write", *, throttle: bool = True) -> Optional[Path]:
    """把 path 复制一份到同目录 backups/，返回备份路径；源不存在 → None。

    - 备份名：<原名>.<tag>.<YYYYMMDD-HHMMSS.微秒><后缀>，同秒多次写也不撞名。
    - throttle=True（默认）：同一进程同一路径 60 秒内只备份一次；
      reset_to_initial() 等关键动作传 throttle=False 强制备份。
    - 失败静默返回 None：备份是保险，不是主链（旧口径：记账绝不因备份失败中断）。
    """
    src = Path(path)
    try:
        if not src.is_file():
            return None
        key = str(src.resolve())
    except OSError:
        return None
    now = time.time()
    with _BACKUP_LOCK:
        if throttle:
            last = _BACKUP_TS.get(key)
            if last is not None and (now - last) < BACKUP_THROTTLE_S:
                return None
        _BACKUP_TS[key] = now
    try:
        folder = src.parent / BACKUPS_NAME
        folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
        stamp += ".%06d" % (int(now * 1e6) % 1000000)
        dst = folder / ("%s.%s.%s%s" % (src.stem, tag, stamp, src.suffix))
        shutil.copy2(src, dst)
        _prune_backups(folder, "%s.%s." % (src.stem, tag))
        return dst
    except OSError:
        return None


# ------------------------------------------------------------------ 原子写
def _replace_retry(src: Path, dst: Path, retries: int = 6, poll_s: float = 0.05) -> None:
    """Windows 原子替换 + 短重试（旧 P2-3 口径，嗅嗅审查 2026-09-01 同款）。

    os.replace 在 Windows 上与目标文件被其他进程短暂打开（读）时可能 PermissionError；
    账本写路径即便在统一锁内仍偶发——重试数次兜底，仍失败才抛（**绝不静默吞错账**）。
    """
    last = None
    for _ in range(max(1, retries)):
        try:
            os.replace(src, dst)
            return
        except OSError as e:  # noqa: BLE001 - PermissionError / 共享冲突短窗口
            last = e
            time.sleep(poll_s)
    if last is not None:
        raise last


def atomic_write_json(path: Any, doc: Any, *, audit_event: str = "",
                      detail: str = "") -> None:
    """原子写 JSON：tmp(pid+thread) → fsync → os.replace 重试 6 次；失败抛异常。

    流程：
      1. backup_file() 写前快照（60s 节流；源不存在则不备份，首写无历史可保）；
      2. tmp 文件名带 pid + thread id，多进程/多线程并发写不会互相踩 tmp；
      3. flush + os.fsync 落盘后再 replace，断电不会留下半个 JSON；
      4. os.replace 6 次 × 50ms 重试（Windows 读方短窗口），仍失败 → 抛；
      5. 写一条审计（event 缺省 "write"，detail 缺省 = 目标文件名 + 字节数）。

    **失败必须抛**：账本不能静默丢（调用方按业务异常往上抛，不"假装成功"）。
    """
    p = Path(path)
    backup_file(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(doc, ensure_ascii=False, indent=2)
    tmp = p.parent / ("%s.%d.%d.tmp" % (p.name, os.getpid(), threading.get_ident()))
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        _replace_retry(tmp, p)
    finally:
        if tmp.exists():
            try:
                os.remove(tmp)
            except OSError:
                pass
    audit(audit_event or "write",
          detail or ("%s %dB" % (p.name, len(payload.encode("utf-8")))),
          root=p.parent)


# ------------------------------------------------------------------ 锁
def locked(path: Any, timeout_s: float = 10.0):
    """跨进程排他锁上下文（锁哨兵 = <path>.lock）。

    直接复用 vendored filelock.exclusive_lock；超时**不在这里吞**——
    broker 的 _ledger_lock 要捕获 FileLockTimeout 做 fail-open + 告警留痕，
    调用方自行决定降级策略（本层不擅自直通，避免掩盖丢更新）。

        with state.locked(paths.trades):
            ...读-改-写...
    """
    return exclusive_lock(path, timeout_s=timeout_s)


@contextmanager
def _noop_lock() -> Iterator[None]:
    """无可锁原语时的直通上下文（仅内部备用；正常路径不走这里）。"""
    yield


# ------------------------------------------------------------------ 重置
def reset_to_initial(root: Optional[Any] = None,
                     initial_cash: float = INITIAL_CASH) -> Dict[str, Any]:
    """归档当前账本 → 重建「空账户 + 空流水」；返回摘要（4.1.6 一键回初始的实现）。

    - 归档：把现存 account / trades / conditions / freeze / manual_lock 复制到
      backups/reset_<ts>/（复制不移动：原文件随后被空账本覆盖，但旧副本可整份拿回）；
    - 重建：paper_account.json = 初始现金、空持仓、equity_curve 一条当日初始值；
      paper_trades.json = 空 trades 数组（schema_version 与原口径一致）；
    - **不动** kill_switch.json（急停是人拍板的开关，重置账本不该顺手解除急停）；
    - 不动 engine_state.json（只读，trade_mode 恒 paper）。

    返回 {"root", "backup_dir", "archived", "initial_cash", "cash", "trade_date"}。
    """
    pp = get_paths(root)
    ts = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    ts += ".%06d" % (int(time.time() * 1e6) % 1000000)
    dest = pp.backups / ("reset_%s" % ts)
    archived = []
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError:
        dest = pp.backups
    for src in (pp.account, pp.trades, pp.conditions, pp.freeze,
                pp.manual_lock, pp.kill_switch):
        try:
            if src.is_file():
                shutil.copy2(src, dest / src.name)
                archived.append(src.name)
        except OSError:
            continue

    day = now_str()[:10]
    cash = float(initial_cash)
    acct = {
        "initial_cash": cash,
        "cash": cash,
        "positions": {},
        "realized_pnl": 0.0,
        "equity_curve": [{"date": day, "total": round(cash, 2), "mtm": True,
                          "mtm_source": "close", "missing": [], "marks": {}}],
        "stats": {"win_rate": 0.0, "closed_trades": 0, "wins": 0},
        "updated_at": now_str(),
        "trade_date": day,
        "source": "paper_reset_to_initial",
    }
    atomic_write_json(pp.account, acct, audit_event="reset.account",
                      detail="reset_to_initial 初始资金 %.2f（旧账本归档 %d 个文件）"
                             % (cash, len(archived)))
    atomic_write_json(pp.trades,
                      {"schema_version": "1.0.0", "updated_at": now_str(), "trades": []},
                      audit_event="reset.trades", detail="reset_to_initial 清空成交流水")
    return {"root": str(pp.root),
            "backup_dir": str(dest) if archived else "",
            "archived": archived,
            "initial_cash": cash,
            "cash": cash,
            "trade_date": day}
