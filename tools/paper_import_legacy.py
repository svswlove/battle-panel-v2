#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/paper_import_legacy.py · 把旧 8800 的纸面账本一次性导入 panel-v2（4.1.6）

背景：新版纸面账本放在独立目录（paths.PAPER_STATE_DIR），绝不与旧 8800 双写。
迁移/首次启用时用本工具把旧 `engine/state/paper_account.json` 与
`paper_trades.json` 复制到新目录，保留账户现金/持仓/历史流水。

用法：
  python tools/paper_import_legacy.py --dry-run
  python tools/paper_import_legacy.py --yes
  python tools/paper_import_legacy.py --yes --include-conditions

安全口径：
- 默认只预览；--yes 才写。
- 目标已有账本且未 --force → 拒绝（不静默覆盖）。
- 写前把目标旧文件备份到 <root>/backups/import_<ts>/。
- 源文件只读；不修改 engine/state 一个字节。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.paper import state  # noqa: E402

WS = ROOT.parents[1] if len(ROOT.parents) >= 2 else ROOT.parent
DEFAULT_SOURCE = WS / "engine" / "state"


def _read_json(path: Path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _summarize(account, trades, conditions=None) -> dict:
    acct = account if isinstance(account, dict) else {}
    rows = trades.get("trades") if isinstance(trades, dict) else trades
    rows = rows if isinstance(rows, list) else []
    conds = conditions.get("orders") if isinstance(conditions, dict) else conditions
    conds = conds if isinstance(conds, list) else []
    return {
        "cash": acct.get("cash"),
        "initial_cash": acct.get("initial_cash"),
        "positions": len(acct.get("positions") or {}),
        "realized_pnl": acct.get("realized_pnl"),
        "trades": len(rows),
        "trade_date": acct.get("trade_date"),
        "conditions": len(conds),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="导入旧 8800 纸面账本到 panel-v2")
    ap.add_argument("--source", default=str(DEFAULT_SOURCE), help="旧 engine/state 目录")
    ap.add_argument("--root", default=None, help="目标账本目录（默认 PAPER_STATE_DIR）")
    ap.add_argument("--include-conditions", action="store_true",
                    help="同时导入 conditional_orders.json（默认不导入，避免旧条件单在新系统被重新触发）")
    ap.add_argument("--force", action="store_true", help="目标账本已存在时仍覆盖（会先备份）")
    ap.add_argument("--yes", action="store_true", help="确认写入（不带只预览）")
    ap.add_argument("--dry-run", action="store_true", help="强制只预览")
    args = ap.parse_args(argv)

    src = Path(args.source)
    if not src.is_dir():
        print(f"源目录不存在：{src}", file=sys.stderr)
        return 2
    src_account = src / "paper_account.json"
    src_trades = src / "paper_trades.json"
    if not src_account.is_file():
        print(f"缺少源账本：{src_account}", file=sys.stderr)
        return 2
    try:
        account = _read_json(src_account)
        trades = _read_json(src_trades) if src_trades.is_file() else {"trades": []}
        conditions = None
        if args.include_conditions:
            p = src / "conditional_orders.json"
            conditions = _read_json(p) if p.is_file() else {"orders": []}
    except (OSError, ValueError) as exc:
        print(f"源文件读取失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if not isinstance(account, dict) or "cash" not in account:
        print("源 paper_account.json 不是有效账户（缺 cash）", file=sys.stderr)
        return 2

    dst = state.get_paths(args.root)
    summary = _summarize(account, trades, conditions)
    print("源：", src)
    print("目标：", dst.root)
    print("摘要：", json.dumps(summary, ensure_ascii=False, indent=2))

    existing = [p.name for p in (dst.account, dst.trades, dst.conditions) if p.is_file()]
    if existing and not args.force:
        print(f"\n目标已有账本 {existing}；未加 --force，拒绝覆盖。", file=sys.stderr)
        return 3
    if args.dry_run or not args.yes:
        print("\n[预览] 未写入。确认导入请加 --yes（覆盖已有账本再加 --force）。")
        return 0

    # 备份目标旧文件
    ts = state.now_str().replace("-", "").replace(":", "").replace(" ", "_")
    backup_dir = dst.backups / f"import_{ts}"
    archived = []
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        backup_dir = dst.backups
    for p in (dst.account, dst.trades, dst.conditions):
        if p.is_file():
            try:
                shutil.copy2(p, backup_dir / p.name)
                archived.append(p.name)
            except OSError:
                pass

    state.atomic_write_json(dst.account, account, audit_event="import_legacy.account",
                            detail=f"from {src_account}")
    state.atomic_write_json(dst.trades, trades, audit_event="import_legacy.trades",
                            detail=f"from {src_trades}")
    if conditions is not None:
        state.atomic_write_json(dst.conditions, conditions, audit_event="import_legacy.conditions",
                                detail=f"from {src / 'conditional_orders.json'}")
    print("\n导入完成：")
    print(json.dumps({"root": str(dst.root), "backup_dir": str(backup_dir) if archived else "",
                      "archived": archived, "summary": summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
