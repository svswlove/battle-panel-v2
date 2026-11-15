# -*- coding: utf-8 -*-
"""生成「真实轨迹 + idx_chg 补值」样本（tests/js/evo_mode.cdp.mjs 用；只读生产文件，写 tests/fixtures）。

用法（在 panel-v2 根目录）：python app/plugins/intraday/tests/gen_track_filled.py
"""
import importlib.util
import json
import sys
from pathlib import Path

PANEL = Path(__file__).resolve().parents[4]          # tests/ → intraday/ → plugins/ → app/ → panel-v2
sys.path.insert(0, str(PANEL))
from app.core import quotes as q  # noqa: E402

WS = PANEL.parents[1]        # panel-v2 → projects → 工作区根
TRACK = WS / "data" / "panel" / "amv_live_sector_track.json"
OUT = Path(__file__).resolve().parent / "fixtures" / "track_filled_20261027.json"


class Ctx:
    def __init__(self, doc):
        self.doc = doc

    def panel_json(self, name):
        return self.doc

    def db_read(self, name):
        raise FileNotFoundError(name)

    @property
    def quotes(self):
        return q


spec = importlib.util.spec_from_file_location(
    "cs_gen", PANEL / "app" / "plugins" / "compat_sector" / "backend.py")
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

doc = json.loads(TRACK.read_text(encoding="utf-8"))
latest = max(doc["dates"], key=lambda k: k.replace("-", ""))
snaps = mod.fill_idx_chg(Ctx(doc), doc["dates"][latest], latest)
# 只保留「有 idx_chg 的最新一日」，模拟重启后 /sector_track.json 的真实响应
body = {
    "date": latest,
    "snapshots": snaps,
    "boards": (snaps[-1].get("boards") if snaps else {}) or {},
    "weights": {},
    "weights_date": None,
    "stale": False,
    "stale_reason": "",
    "last_ts": (snaps[-1].get("ts") if snaps else None),
    "meta": doc.get("meta") or {},
}
# 权重从生产 weights 段补（前端四象/贡献要用）
weights = {}
try:
    import sqlite3
    db = WS / "data" / "amv_board.db"
    if not db.is_file():
        db = WS / "data" / "amv_board.db"
    conn = sqlite3.connect("file:%s?mode=ro" % str(db).replace("\\", "/"), uri=True)
    row = conn.execute("SELECT MAX(date) FROM sector_daily WHERE level='L2' AND date<?",
                       ("%s-%s-%s" % (latest[:4], latest[4:6], latest[6:]),)).fetchone()
    if row and row[0]:
        for code, share in conn.execute(
                "SELECT code,share FROM sector_daily WHERE level='L2' AND date=? AND code<>'BK9999'",
                (row[0],)):
            if share is not None:
                weights[code] = round(float(share) * 100, 4)
        body["weights_date"] = row[0]
    conn.close()
except Exception as exc:  # noqa: BLE001
    print("weights skip:", exc)
body["weights"] = weights

OUT.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
filled = sum(1 for s in snaps if s.get("idx_chg"))
codes = sorted({c for s in snaps for c in (s.get("idx_chg") or {})})
print("date=%s snaps=%d filled=%d codes=%s weights=%d size=%.1fKB"
      % (latest, len(snaps), filled, codes, len(weights), OUT.stat().st_size / 1024))
