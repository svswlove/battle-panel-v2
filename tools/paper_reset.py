#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/paper_reset.py · 纸面账本一键回到初始态（4.1.6）

用法：
  python tools/paper_reset.py                 # 只预览，不删不改
  python tools/paper_reset.py --yes           # 归档当前账本 → 重建 50 万空仓
  python tools/paper_reset.py --yes --initial-cash 300000

安全口径：
- 默认必须显式 --yes；否则只打印当前账本摘要，不做任何写操作。
- 归档目录 = <root>/backups/reset_<yyyyMMdd-HHmmss.微秒>/，旧文件复制不移动。
- 只动纸面账本目录（paths.PAPER_STATE_DIR / PANEL_V2_PAPER_DIR）；
  绝不碰 engine/state、data/panel。
- 不解除 kill_switch.json 的急停状态（重置账本不等于解除急停）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.paper import state  # noqa: E402


def _summary(root: str | None) -> dict:
    p = state.get_paths(root)
    acct = state.read_json(p.account, {}) or {}
    trades = state.read_json(p.trades, {}) or {}
    rows = trades.get("trades") if isinstance(trades, dict) else trades
    rows = rows if isinstance(rows, list) else []
    return {
        "root": str(p.root),
        "account_exists": p.account.exists(),
        "cash": acct.get("cash"),
        "positions": len(acct.get("positions") or {}),
        "trades": len(rows),
        "trade_date": acct.get("trade_date"),
        "kill_switch": bool((state.read_json(p.kill_switch, {}) or {}).get("active")),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="纸面账本一键回到初始态")
    ap.add_argument("--root", default=None, help="账本目录（默认 PANEL_V2_PAPER_DIR / paths.PAPER_STATE_DIR）")
    ap.add_argument("--initial-cash", type=float, default=state.INITIAL_CASH,
                    help="初始资金（默认 500000）")
    ap.add_argument("--yes", action="store_true", help="确认执行（不带只预览）")
    args = ap.parse_args(argv)

    before = _summary(args.root)
    print("当前纸面账本：")
    print(json.dumps(before, ensure_ascii=False, indent=2))
    if not args.yes:
        print("\n[预览] 未执行。确认要归档并重置请加 --yes。")
        return 0
    if args.initial_cash <= 0:
        print("初始资金必须 > 0", file=sys.stderr)
        return 2
    result = state.reset_to_initial(args.root, initial_cash=args.initial_cash)
    print("\n已重置：")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("\n下一步：重启 8810（或等保活任务拉起）后，面板即为初始态。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
