#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/feed_probe.py · QMT 58610 第二订阅者活体验证（4.1.1 周一补测）

用法（**仅交易时段、QMT 客户端已登录时**）：
  python tools/feed_probe.py --seconds 20
  python tools/feed_probe.py --codes 000001.SH,600519.SH --seconds 20

判定：
- 打印 seq（非空）+ tick_count>0 → 第二订阅者可用；
- 同时旧 tick_feed/旧 server 仍在跑（看 engine/state/tick_feed_status.json mtime）→
  证明“多订阅者并存”成立；
- 连不上/0 tick → 记录错误，不修改任何生产文件。

本脚本只读 QMT 行情，不发单、不写文件（除 stdout）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QMT_DIR = ROOT.parents[1] / "envs" / "qmt312" if len(ROOT.parents) >= 2 else ROOT / "envs" / "qmt312"
if str(QMT_DIR) not in sys.path:
    sys.path.insert(0, str(QMT_DIR))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="QMT 58610 第二订阅者探针")
    ap.add_argument("--codes", default="000001.SH,600519.SH")
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=58610)
    args = ap.parse_args(argv)
    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    try:
        from xtquant import xtdata
    except Exception as exc:  # noqa: BLE001
        print(f"xtquant import 失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    stats = {"ticks": 0, "codes": set(), "first": None, "last": None}

    def cb(datas):
        now = time.strftime("%H:%M:%S")
        stats["ticks"] += len(datas or {})
        stats["codes"].update((datas or {}).keys())
        if stats["first"] is None:
            stats["first"] = now
        stats["last"] = now

    try:
        xtdata.connect(ip=args.host, port=args.port)
    except Exception as exc:  # noqa: BLE001
        print(f"connect 失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    seq = xtdata.subscribe_whole_quote(codes, cb)
    print(f"subscribed seq={seq!r} codes={codes}")
    deadline = time.time() + max(1.0, args.seconds)
    while time.time() < deadline:
        time.sleep(0.5)
    print(json.dumps({"seq": seq, "tick_count": stats["ticks"],
                      "codes_seen": sorted(stats["codes"]),
                      "first_tick": stats["first"], "last_tick": stats["last"]},
                     ensure_ascii=False))
    try:
        xtdata.unsubscribe_quote(seq)
        xtdata.disconnect()
    except Exception:  # noqa: BLE001
        pass
    return 0 if stats["ticks"] > 0 else 4


if __name__ == "__main__":
    raise SystemExit(main())
