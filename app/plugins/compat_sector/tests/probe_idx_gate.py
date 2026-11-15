# -*- coding: utf-8 -*-
"""只读探针：历史日期请求不拉行情、当日补值覆盖率。不写任何文件。"""
import importlib.util
import json
import sys
from pathlib import Path

PANEL = Path(__file__).resolve().parents[4]      # tests/ → compat_sector/ → plugins/ → app/ → panel-v2
sys.path.insert(0, str(PANEL))
from app.core import quotes as q  # noqa: E402

calls = []
_orig_min = q.minute_bars


def _spy_min(glb, count=241):
    calls.append(glb)
    return _orig_min(glb, count)


q.minute_bars = _spy_min


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
    "cs_probe", PANEL / "app" / "plugins" / "compat_sector" / "backend.py")
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

WS = PANEL.parents[1]        # panel-v2 → projects → 工作区根
TRACK = WS / "data" / "panel" / "amv_live_sector_track.json"
doc = json.loads(TRACK.read_text(encoding="utf-8"))
ctx = Ctx(doc)

for d in sorted(doc["dates"]):
    calls.clear()
    mod._IDX_CACHE.clear()
    body = mod.build_sector_track(ctx, d)
    filled = sum(1 for s in body["snapshots"] if s.get("idx_chg"))
    print("%s: snaps=%d filled=%d quoteCalls=%d codes=%s"
          % (d, len(body["snapshots"]), filled, len(calls),
             sorted({c for s in body["snapshots"] for c in (s.get("idx_chg") or {})})))
    print("   keys:", sorted(body.keys()))
    print("   prod file untouched:", not any("idx_chg" in s for s in doc["dates"][d]))
