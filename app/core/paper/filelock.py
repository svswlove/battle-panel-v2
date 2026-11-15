# -*- coding: utf-8 -*-
"""app/core/paper/filelock.py · 跨进程文件互斥锁（Windows msvcrt.locking）

【来源】旧 engine/common/filelock.py 逐字 vendoring（2026-10-23 任务书 4.1.2）。
只改模块名与这行来源说明，逻辑一行不动（含 fail-open/超时口径注释）。
纯标准库、零业务依赖、无 import 副作用：纸面账本锁的唯一载体。

2026-08-27 QMT 执行链工单（P7）：auto_buy_progress.json 由多票进程并发
read-modify-write，后写覆盖前写。本模块提供目录级排他锁，把 RMW 窗口归零，
不动单文件 schema（dashboard_builder / daily_cap / 现有单测零改动）。

用法：
    from common.filelock import exclusive_lock
    with exclusive_lock(path_or_dir, timeout_s=10.0):
        ... 临界区（读-改-写目标文件） ...

机制（Windows 主战场）：
- 锁载体 = <target>.lock 哨兵文件（内容无意义，只借内核强制字节区间锁）
- msvcrt.locking(fd, LK_NBLCK, 1)：对首字节申请非阻塞独占锁；占用中 OSError →
  sleep(poll) 重试至 timeout 抛 TimeoutError（调用方自行降级，绝不死等死锁）
- 释放：seek 回首字节 + LK_UNLCK；进程崩溃内核自动释放（哨兵文件无需清理）
- 非 Windows（开发机跑 POSIX 单测）：fcntl.flock 等价语义；两者皆缺 → 直通模式
  （无锁裸跑：单测/CI 场景可接受，生产固定 Windows 不受影响）

铁律：锁持有时间必须短（毫秒级 RMW）；锁内禁止网络 IO / 长 CPU 计算。
"""
import contextlib
import os
import time

try:
    import msvcrt  # noqa: F401 - Windows 生产路径
except ImportError:
    msvcrt = None

try:
    import fcntl  # noqa: F401 - POSIX 开发路径
except ImportError:
    fcntl = None


class FileLockTimeout(Exception):
    """锁等待超时（调用方捕获后静默降级/告警，不得无限重试）。"""


def lock_path_for(target_path):
    """<any>.json → <any>.json.lock（与目标文件同目录，权限随父目录）。"""
    return str(target_path) + ".lock"


@contextlib.contextmanager
def exclusive_lock(target, timeout_s=10.0, poll_s=0.05):
    """跨进程排他锁上下文管理器。
    target：任意路径（通常为要保护的 state JSON）；锁哨兵 = target + ".lock"。
    timeout_s：总等待预算，超时抛 FileLockTimeout（进程别挂着不动）。
    获取成功 yield 哨兵路径；无论异常与否最终释放 + 关句柄。"""
    sentinel = lock_path_for(target)
    os.makedirs(os.path.dirname(sentinel) or ".", exist_ok=True)
    fd = os.open(sentinel, os.O_CREAT | os.O_RDWR)
    acquired = False
    deadline = time.monotonic() + max(timeout_s, 0.0)
    try:
        while True:
            try:
                if msvcrt is not None:                       # Windows
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                elif fcntl is not None:                      # POSIX
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:                                        # 直通：无原语可用
                    break
                acquired = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise FileLockTimeout(
                        f"file lock 超时（>{timeout_s:.1f}s）：{sentinel}") from None
                time.sleep(poll_s)
        yield sentinel
    finally:
        if acquired:
            try:
                if msvcrt is not None:
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                elif fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass  # 释放失败不再抛（进程退出内核必然回收），保留 close 兜底
        os.close(fd)
