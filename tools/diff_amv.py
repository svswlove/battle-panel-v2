# -*- coding: utf-8 -*-
"""amv 三份原始 JSON 新旧端点精确比对（只读 HTTP，不连库）。

用法：
  python tools/diff_amv.py --old http://127.0.0.1:8800 --new http://127.0.0.1:8810
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

PAIRS = [
    ("amv_live", "/api/amv_live.json", "/api/p/amv/live"),
    ("amv_regime_latest", "/api/amv_regime_latest.json", "/api/p/amv/regime"),
    ("position_cap_latest", "/api/position_cap_latest.json", "/api/p/amv/position_cap"),
]


def get(base: str, path: str) -> object:
    with urllib.request.urlopen(base.rstrip("/") + path, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", default="http://127.0.0.1:8800")
    ap.add_argument("--new", default="http://127.0.0.1:8810")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    results = []
    ok = True
    for name, old_path, new_path in PAIRS:
        old = get(args.old, old_path)
        new = get(args.new, new_path)
        same = old == new
        ok = ok and same
        results.append({"name": name, "old": old_path, "new": new_path, "equal": same})
        print(f"{name}: equal={same}")

    payload = {"ok": ok, "old": args.old, "new": args.new, "pairs": results}
    if args.out:
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
