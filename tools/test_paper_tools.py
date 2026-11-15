# -*- coding: utf-8 -*-
"""4.1.6 纸面账本工具测试（tmp 目录，不碰真实账本）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import paper_import_legacy, paper_reset  # noqa: E402


def _write(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def test_import_legacy_dry_run_and_yes(tmp_path: Path) -> None:
    src = tmp_path / "engine_state"
    _write(src / "paper_account.json", {"cash": 123456.0, "positions": {}, "initial_cash": 500000})
    _write(src / "paper_trades.json", {"trades": [{"paper_order_id": "PP1", "status": "FILLED"}]})
    dst = tmp_path / "paper"
    assert paper_import_legacy.main(["--source", str(src), "--root", str(dst), "--dry-run"]) == 0
    assert not (dst / "paper_account.json").exists()
    assert paper_import_legacy.main(["--source", str(src), "--root", str(dst), "--yes"]) == 0
    acct = json.loads((dst / "paper_account.json").read_text(encoding="utf-8"))
    assert acct["cash"] == 123456.0
    # 第二次无 --force 必须拒绝
    assert paper_import_legacy.main(["--source", str(src), "--root", str(dst), "--yes"]) == 3


def test_reset_preview_and_yes(tmp_path: Path) -> None:
    dst = tmp_path / "paper"
    _write(dst / "paper_account.json", {"cash": 1.0, "positions": {"600519.SH": {"qty": 100}}})
    _write(dst / "paper_trades.json", {"trades": [{"paper_order_id": "PP1"}]})
    assert paper_reset.main(["--root", str(dst)]) == 0
    assert json.loads((dst / "paper_account.json").read_text(encoding="utf-8"))["cash"] == 1.0
    assert paper_reset.main(["--root", str(dst), "--yes", "--initial-cash", "500000"]) == 0
    acct = json.loads((dst / "paper_account.json").read_text(encoding="utf-8"))
    assert acct["cash"] == 500000.0
    assert acct["positions"] == {}
    trades = json.loads((dst / "paper_trades.json").read_text(encoding="utf-8"))
    assert trades["trades"] == []
    assert any(p.name.startswith("reset_") for p in (dst / "backups").iterdir())
