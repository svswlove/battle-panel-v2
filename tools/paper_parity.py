#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/paper_parity.py · 纸面账本新旧端逐字段对比（4.1.5）

前提：旧 8800 与新 8810 都在跑，且新账本已用 `paper_import_legacy.py` 导入旧账本。
对比：
  /api/paper_account
  /api/order/list
  /api/condition/list
  /api/ledger?mode=paper
  /api/trades/{code}（取旧纸面流水里出现过的前 5 个代码）

输出每个端点的「键集合 / 值是否全等 / 差异摘要」；退出码 0 = 全等，1 = 有差异。
只读，不写任何文件。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.paper import state  # noqa: E402


def _get(base: str, path: str):
    with urllib.request.urlopen(base + path, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _diff_keys(a, b, prefix="") -> list[str]:
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append(f"{prefix}{k}: 新端独有")
            elif k not in b:
                out.append(f"{prefix}{k}: 旧端独有")
            else:
                out.extend(_diff_keys(a[k], b[k], prefix + k + "."))
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{prefix}[] 长度 {len(a)} vs {len(b)}")
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                out.extend(_diff_keys(x, y, f"{prefix}[{i}]."))
    elif a != b:
        out.append(f"{prefix}: {a!r} != {b!r}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="纸面账本新旧端逐字段对比")
    ap.add_argument("--old", default="http://127.0.0.1:8800")
    ap.add_argument("--new", default="http://127.0.0.1:8810")
    ap.add_argument("--max-trades", type=int, default=5)
    args = ap.parse_args(argv)

    checks = ["/api/paper_account", "/api/order/list", "/api/condition/list",
              "/api/ledger?mode=paper"]
    paths = state.get_paths()
    doc = state.read_json(paths.trades, {}) or {}
    rows = doc.get("trades") if isinstance(doc, dict) else doc
    codes = []
    for r in rows if isinstance(rows, list) else []:
        c = str((r or {}).get("ts_code") or "")
        if c and c not in codes:
            codes.append(c)
    checks += [f"/api/trades/{c}?days=60" for c in codes[:max(1, args.max_trades)]]

    failed = 0
    for path in checks:
        try:
            old = _get(args.old, path)
            new = _get(args.new, path)
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {path}: {type(exc).__name__}: {exc}")
            failed += 1
            continue
        diffs = _diff_keys(old, new)
        if diffs:
            failed += 1
            print(f"FAIL {path}: {len(diffs)} 处差异")
            for d in diffs[:8]:
                print("   ", d)
        else:
            print(f"OK   {path}: 逐字段全等")
    print(f"\n合计 {len(checks)} 项：{'全部一致' if not failed else str(failed) + ' 项不一致'}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
