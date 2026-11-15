#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""作战面板 v2 桌面启动器（无第三方依赖）。

只做三件事：确保 8810 在跑、打开浏览器、退出。
以后可用 PyInstaller 把本文件封成 exe；业务仍在 app/ 插件里，不在启动器里。

用法：
  python launcher.py             # 启动并打开浏览器
  python launcher.py --no-browser
  python launcher.py --check     # 只检查，不启动/不打开
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
URL = "http://127.0.0.1:8810"
HEALTH = URL + "/api/health"


def healthy(timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(HEALTH, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return resp.status == 200 and data.get("ok") is True
    except Exception:  # noqa: BLE001
        return False


def port_open(timeout: float = 0.5) -> bool:
    """端口是否已监听。与 ensure_8810.ps1 同语义：监听但健康未过 = 启动中，不重复拉起。"""
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        return sock.connect_ex(("127.0.0.1", 8810)) == 0
    finally:
        sock.close()


def start() -> None:
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "run.ps1")],
        cwd=str(ROOT),
        creationflags=flags,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    if not healthy():
        if args.check:
            print("8810 未运行")
            return 1
        if port_open():
            print("8810 端口已监听但健康未就绪，按启动中处理，等待而不重复拉起")
        else:
            print("8810 未运行，启动中…")
            start()
        deadline = time.time() + 30
        while time.time() < deadline:
            if healthy():
                break
            time.sleep(1)
        else:
            print("启动超时：请查看 run.ps1 / 8810 日志")
            return 1

    print("panel v2 -> " + URL)
    if not args.no_browser and not args.check:
        webbrowser.open(URL)
    return 0


if __name__ == "__main__":
    sys.exit(main())
