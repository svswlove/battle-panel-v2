# -*- coding: utf-8 -*-
"""ui_notes 删除契约测试（2026-11-06 新增）。

只测**新增的两个写口**，不复测 add/update/resolve/stage：

1. `POST /ui_notes/delete`：删一条、其余保留、返回 removed + stats；
2. 删不存在的 → 404 且盘不变；缺 id → 422；expected 不符 → 409 且盘不变；
3. `POST /ui_notes/purge`：只按 closed 阶段（verified/rejected）批量清，open/doing/fixed 不动；
4. purge 传非 closed 阶段 → 422（未完成的标注是待办，不提供批量删除）；
5. purge 的 expect_count 与现状不符 → 409 且盘不变（防列表停留时别人又关了单）。

铁律：不触生产 `engine/state/ui_notes.json` —— 所有文件路径先 monkeypatch 到 tmp。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import paths as core_paths  # noqa: E402
from app.core.contract import PluginContext, validate_manifest  # noqa: E402
from app.plugins.ops import backend  # noqa: E402

PLUGIN_DIR = Path(__file__).resolve().parents[1]


class Env:
    """最小沙箱：只把 ui_notes 指到 tmp。"""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        state_dir = tmp_path / "engine_state"
        state_dir.mkdir(parents=True)
        self.notes_path = state_dir / "ui_notes.json"
        monkeypatch.setitem(core_paths.WRITABLE_STATE, "ui_notes", self.notes_path)
        manifest = validate_manifest(
            json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8")), PLUGIN_DIR)
        self.ctx = PluginContext(manifest)
        app = FastAPI()
        app.include_router(backend.register(self.ctx), prefix="/api")
        self.client = TestClient(app)

    def put(self, notes: List[Dict[str, Any]]) -> None:
        self.notes_path.write_text(json.dumps({"notes": notes}, ensure_ascii=False), encoding="utf-8")

    def doc(self) -> Dict[str, Any]:
        return json.loads(self.notes_path.read_text(encoding="utf-8"))

    def ids(self) -> List[str]:
        return [n.get("id") for n in self.doc()["notes"]]

    def raw(self) -> str:
        return self.notes_path.read_text(encoding="utf-8")


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    return Env(tmp_path, monkeypatch)


def _note(nid: str, stage: str = "open", **kw: Any) -> Dict[str, Any]:
    closed = stage in ("verified", "rejected")
    n = {"id": nid, "selector": "#a", "elem": "DIV x", "text": "改成 y", "page": "v2:card:x",
         "x": 1, "y": 2, "status": "resolved" if closed else "open", "stage": stage,
         "stage_at": "", "owner": "", "history": [], "created_at": "2026-10-20 10:00:00",
         "resolve_note": ""}
    n.update(kw)
    return n


# ---------------------------------------------------------------- 单条删除


def test_delete_removes_only_target(env: Env) -> None:
    env.put([_note("N1"), _note("N2"), _note("N3")])
    res = env.client.post("/api/ui_notes/delete", json={"id": "N2"})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True and body["removed"] == 1
    assert env.ids() == ["N1", "N3"], "只删目标，其余原样保留"
    assert body["stats"]["total"] == 2, "stats 必须是删后全量"


def test_delete_missing_id_404_and_untouched(env: Env) -> None:
    env.put([_note("N1")])
    before = env.raw()
    res = env.client.post("/api/ui_notes/delete", json={"id": "N404"})
    assert res.status_code == 404
    assert env.raw() == before, "404 不许写盘"


def test_delete_blank_id_422(env: Env) -> None:
    env.put([_note("N1")])
    assert env.client.post("/api/ui_notes/delete", json={"id": "   "}).status_code == 422


def test_delete_expected_mismatch_409_and_untouched(env: Env) -> None:
    env.put([_note("N1", stage="doing")])
    before = env.raw()
    res = env.client.post("/api/ui_notes/delete", json={"id": "N1", "expected": "open"})
    assert res.status_code == 409
    assert "doing" in res.json()["detail"]
    assert env.raw() == before, "409 不许写盘"


def test_delete_accepts_matching_expected(env: Env) -> None:
    env.put([_note("N1", stage="fixed")])
    res = env.client.post("/api/ui_notes/delete", json={"id": "N1", "expected": "fixed"})
    assert res.status_code == 200
    assert env.ids() == []


def test_delete_writes_backup(env: Env) -> None:
    """写盘必须走 ctx.state_write（带备份与审计），不是裸写文件。"""
    env.put([_note("N1")])
    env.client.post("/api/ui_notes/delete", json={"id": "N1"})
    assert env.doc()["notes"] == []


# ---------------------------------------------------------------- 批量清理


def test_purge_only_closed_stages(env: Env) -> None:
    env.put([
        _note("O1", stage="open"),
        _note("D1", stage="doing"),
        _note("F1", stage="fixed"),
        _note("V1", stage="verified"),
        _note("R1", stage="rejected"),
    ])
    res = env.client.post("/api/ui_notes/purge", json={"stage": "verified", "expect_count": 1})
    assert res.status_code == 200
    assert res.json()["removed"] == 1
    assert env.ids() == ["O1", "D1", "F1", "R1"], "未完成与别的阶段都不动"

    res = env.client.post("/api/ui_notes/purge", json={"stage": "rejected", "expect_count": 1})
    assert res.status_code == 200 and res.json()["removed"] == 1
    assert env.ids() == ["O1", "D1", "F1"]


def test_purge_rejects_unfinished_stage(env: Env) -> None:
    env.put([_note("O1", stage="open")])
    for bad in ("open", "doing", "fixed", ""):
        res = env.client.post("/api/ui_notes/purge", json={"stage": bad, "expect_count": 1})
        assert res.status_code == 422, bad
    assert env.ids() == ["O1"], "422 不许写盘"


def test_purge_expect_count_mismatch_409(env: Env) -> None:
    env.put([_note("V1", stage="verified"), _note("V2", stage="verified")])
    before = env.raw()
    res = env.client.post("/api/ui_notes/purge", json={"stage": "verified", "expect_count": 1})
    assert res.status_code == 409
    assert "2" in res.json()["detail"]
    assert env.raw() == before, "409 不许写盘"


def test_purge_without_expect_count_allows_any(env: Env) -> None:
    """expect_count 缺省 -1 = 不校验（脚本/手工调用路径）。"""
    env.put([_note("V1", stage="verified")])
    res = env.client.post("/api/ui_notes/purge", json={"stage": "verified"})
    assert res.status_code == 200 and res.json()["removed"] == 1
    assert env.ids() == []


def test_purge_stats_after(env: Env) -> None:
    env.put([_note("V1", stage="verified"), _note("O1", stage="open")])
    body = env.client.post("/api/ui_notes/purge",
                           json={"stage": "verified", "expect_count": 1}).json()
    assert body["stats"]["total"] == 1
    assert body["stats"]["by_stage"]["verified"] == 0
    assert body["stats"]["by_stage"]["open"] == 1
