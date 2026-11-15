# -*- coding: utf-8 -*-
"""AMV 三份快照：文件 ↔ 8810 端点 逐值一致（2026-10-26 旧端口下线后替代 diff_amv.py）。

为什么换口径：
  `tools/diff_amv.py` 的判据是「8800 响应 == 8810 响应」。但 8800 对这三份也只是
  **读文件再原样返回**：
      amv_live.json            ← engine/executor/amv_live.py（tick_feed/按需刷新写）
      amv_regime_latest.json   ← 夏天-activity/工具/amv_regime_export.py
      position_cap_latest.json ← 夏天-activity/工具/position_cap_amv.py
  所以「8800 == 8810」实质等于「文件 == 8810」。旧端退役后直接比后者，
  更直接、且不再依赖任何旧进程。diff_amv.py 保留（需要对比旧端时可手跑）。

边界：只读文件 + 只发 HTTP GET，不写生产数据。

用法：
    python tools/check_amv_parity.py
    python tools/check_amv_parity.py --new http://127.0.0.1:8810
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PANEL_DIR = ROOT.parent.parent / "data" / "panel"

# (名字, 文件名, 8810 端点)
PAIRS = [
    ("amv_live", "amv_live.json", "/api/p/amv/live"),
    ("amv_regime_latest", "amv_regime_latest.json", "/api/p/amv/regime"),
    ("position_cap_latest", "position_cap_latest.json", "/api/p/amv/position_cap"),
]


def _get_json(base: str, path: str) -> object:
    import urllib.request
    with urllib.request.urlopen(base.rstrip("/") + path, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AMV 快照 文件↔端点 一致性")
    ap.add_argument("--new", default="http://127.0.0.1:8810")
    ap.add_argument("--panel-dir", default=str(PANEL_DIR))
    args = ap.parse_args(argv)

    panel = Path(args.panel_dir)
    ok = True
    for name, fname, path in PAIRS:
        fpath = panel / fname
        if not fpath.is_file():
            # 文件缺失 = 生产者还没跑过。这不是端点退化，单列不判 fail
            # （盘中 amv_live 由按需刷新产出，盘外可能停在上一交易日）。
            print(f"{name}: SKIP（文件不存在：{fpath}）")
            continue
        try:
            disk = json.loads(fpath.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"{name}: FAIL 文件读不出：{type(exc).__name__}: {exc}")
            ok = False
            continue
        try:
            api = _get_json(args.new, path)
        except Exception as exc:  # noqa: BLE001 - 端点不可达如实报错
            print(f"{name}: FAIL 端点不可达 {path}：{type(exc).__name__}: {exc}")
            ok = False
            continue
        same = disk == api
        ok = ok and same
        print(f"{name}: equal={same}")
        if not same:
            # 只报差异规模，不打印全量（文件可达数 MB）
            dk = set(disk) if isinstance(disk, dict) else set()
            ak = set(api) if isinstance(api, dict) else set()
            if dk or ak:
                print(f"  顶层键：仅文件 {sorted(dk - ak)[:5]} / 仅端点 {sorted(ak - dk)[:5]}")
            else:
                print("  顶层键一致，差异在值层（可能端点做了实时覆盖）")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    sys.exit(main())
