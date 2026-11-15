# -*- coding: utf-8 -*-
"""F10 锁与端点的回归守卫（2026-10-27 死锁事故）。

事故：`_F10_LOCK` 原为 `threading.Lock`（不可重入），而调用链是嵌套拿锁的
（`_f10_no_proxy` → `_f10_call` → `_f10_client`），导致所有 F10 调用自锁死。
表象是「网络间歇超时」——因为 python 线程一直阻塞在 acquire 上，
从外部看就是请求永远不返回。排查方向被带偏过，所以立此测试。
"""
import threading

import pytest

import app.core.quotes as Q


def test_f10_lock_is_reentrant():
    """锁必须可重入：外层持锁时，内层再获取不得死锁。"""
    assert isinstance(Q._F10_LOCK, type(threading.RLock())), (
        "F10 锁必须是可重入锁。普通 Lock 会在 _f10_no_proxy → _f10_call → _f10_client "
        "的嵌套调用上自锁死（2026-10-27 事故）"
    )


def test_f10_lock_nesting_does_not_hang():
    """实测嵌套获取（带超时看门狗，真死锁时 pytest 不会永久挂住）。"""
    got = {"depth": 0}

    def body():
        with Q._F10_LOCK:
            with Q._F10_LOCK:
                got["depth"] = 2

    t = threading.Thread(target=body, daemon=True)
    t.start()
    t.join(timeout=3.0)
    assert not t.is_alive(), "嵌套拿 F10 锁 3 秒未返回 → 死锁"
    assert got["depth"] == 2


def test_f10_client_does_not_hang(monkeypatch):
    """`_f10_client()` 全链路（含 endpoint 探测与 no_proxy）不得挂住。"""
    # 把探测要求的真实网络请求替掉：只验锁路径，不验网络
    monkeypatch.setattr(Q, "_f10_build_client", lambda url, host: object())
    monkeypatch.setattr(Q, "_f10_probe_endpoints",
                        lambda: {"base_url": "http://stub", "host": "",
                                 "label": "stub", "probe_ms": 1,
                                 "probed_at": Q.time.monotonic()})
    Q._F10_ENDPOINT.clear()
    Q._F10_CLIENT["cli"] = None
    Q._F10_CLIENT["base_url"] = None

    box = {}

    def body():
        box["cli"] = Q._f10_client()

    t = threading.Thread(target=body, daemon=True)
    t.start()
    t.join(timeout=3.0)
    assert not t.is_alive(), "_f10_client 3 秒未返回 → 锁路径死锁"
    assert box["cli"] is not None
