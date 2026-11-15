# -*- coding: utf-8 -*-
"""ui_notes 生命周期（stage + history）契约测试（2026-10-27 新增）。

只测**新增契约**，不复测旧四件套（那是 test_ops_plugin.py / test_ops_m1_endpoints.py 的事）：

1. 新条从 open 起步、history 为空；
2. 老数据（只有 status）读时推导 stage，且 **list 不写盘**；
3. stage 流转：doing/fixed 保持 status=open，verified/rejected 派生 resolved；
4. 重开清 resolved_at/resolve_note，但 history 保留（可追溯，不是删除）；
5. 乐观并发：expected 不符 → 409；非法 to → 422；缺 id → 404；
6. 旧 `POST /ui_notes/resolve` 仍可用，且等价于 stage→verified（写 history）；
7. 落盘字段向后兼容：status 仍在、旧读法不受影响。

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
    """最小沙箱：只把 ui_notes 指到 tmp，其余白名单保持原样（本文件用不到）。"""

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

    def note(self, nid: str) -> Dict[str, Any]:
        for n in self.doc()["notes"]:
            if n.get("id") == nid:
                return n
        raise AssertionError("落盘里没有 %s" % nid)


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    return Env(tmp_path, monkeypatch)


def _legacy(nid: str = "N1", status: str = "open", **kw: Any) -> Dict[str, Any]:
    n = {"id": nid, "selector": "#a", "elem": "DIV x", "text": "改成 y", "page": "v2:card:x",
         "x": 1, "y": 2, "status": status, "created_at": "2026-10-20 10:00:00",
         "resolve_note": ""}
    n.update(kw)
    return n


# ---------------------------------------------------------------- 读时推导 / 不写盘


def test_add_starts_open_with_empty_history(env: Env) -> None:
    res = env.client.post("/api/ui_notes/add", json={"selector": "#a", "text": "改字号", "page": "v2:card:x"})
    assert res.status_code == 200
    note = res.json()["note"]
    assert note["stage"] == "open" and note["status"] == "open"
    assert note["history"] == [] and note["owner"] == ""
    assert env.note(note["id"])["stage"] == "open", "落盘也要带 stage，别只活在响应里"


def test_list_derives_stage_for_legacy_and_does_not_rewrite(env: Env) -> None:
    env.put([_legacy("N1", "resolved", resolved_at="2026-10-26 00:31:25"),
             _legacy("N2", "open")])
    before = env.notes_path.read_text(encoding="utf-8")
    body = env.client.get("/api/ui_notes/list").json()
    rows = {n["id"]: n for n in body["notes"]}
    assert rows["N1"]["stage"] == "verified" and rows["N1"]["stage_at"] == "2026-10-26 00:31:25"
    assert rows["N2"]["stage"] == "open"
    assert env.notes_path.read_text(encoding="utf-8") == before, "list 是只读视图，不许改盘"


def test_list_stats_and_filters(env: Env) -> None:
    env.put([_legacy("N1", "open", page="v2:card:a"),
             _legacy("N2", "resolved", page="v2:card:a"),
             _legacy("N3", "open", page="v2:card:b", stage="doing", history=[])])
    body = env.client.get("/api/ui_notes/list").json()
    assert body["stats"]["total"] == 3
    assert body["stats"]["by_stage"] == {"open": 1, "doing": 1, "fixed": 0, "verified": 1, "rejected": 0}
    assert body["stats"]["by_page"] == {"v2:card:a": 2, "v2:card:b": 1}
    assert body["stats"]["open"] == 2, "doing 也算未完成"
    only = env.client.get("/api/ui_notes/list", params={"stage": "doing"}).json()
    assert [n["id"] for n in only["notes"]] == ["N3"]
    assert only["stats"]["total"] == 3, "stats 恒为全量，不被过滤影响"
    assert env.client.get("/api/ui_notes/list", params={"stage": "bogus"}).status_code == 422


# ---------------------------------------------------------------- 阶段流转


def test_stage_open_to_doing_keeps_status_open(env: Env) -> None:
    env.put([_legacy("N1")])
    res = env.client.post("/api/ui_notes/stage",
                          json={"id": "N1", "to": "doing", "by": "01", "note": "接手"})
    assert res.status_code == 200
    note = env.note("N1")
    assert note["stage"] == "doing" and note["status"] == "open"
    assert note["owner"] == "01"
    h = note["history"]
    assert len(h) == 1 and (h[0]["from"], h[0]["to"]) == ("open", "doing")
    assert h[0]["note"] == "接手" and h[0]["by"] == "01" and h[0]["at"]


def test_stage_to_verified_closes_and_keeps_old_fields(env: Env) -> None:
    env.put([_legacy("N1")])
    env.client.post("/api/ui_notes/stage", json={"id": "N1", "to": "doing"})
    res = env.client.post("/api/ui_notes/stage",
                          json={"id": "N1", "to": "verified", "note": "已修，见 DONE", "ref": "docs/DONE_x.md"})
    note = env.note("N1")
    assert note["stage"] == "verified" and note["status"] == "resolved"
    assert note["resolved_at"] and note["resolve_note"] == "已修，见 DONE"
    assert note["history"][-1]["ref"] == "docs/DONE_x.md"
    assert len(note["history"]) == 2
    assert res.json()["stats"]["by_stage"]["verified"] == 1


def test_reopen_clears_conclusion_but_keeps_history(env: Env) -> None:
    env.put([_legacy("N1", "resolved", resolved_at="2026-10-26 00:31:25", resolve_note="旧结论")])
    env.client.post("/api/ui_notes/stage", json={"id": "N1", "to": "open", "note": "又出现了"})
    note = env.note("N1")
    assert note["status"] == "open" and note["stage"] == "open"
    assert note["resolved_at"] == "" and note["resolve_note"] == ""
    assert len(note["history"]) == 1, "旧结论从字段清掉，但历史留痕"


def test_stage_expected_mismatch_409(env: Env) -> None:
    env.put([_legacy("N1", stage="doing", history=[])])
    res = env.client.post("/api/ui_notes/stage",
                          json={"id": "N1", "to": "verified", "expected": "open"})
    assert res.status_code == 409
    assert "doing" in res.json()["detail"]
    assert env.note("N1")["stage"] == "doing", "409 时不能写"


def test_stage_rejects_unknown_value_and_missing_id(env: Env) -> None:
    env.put([_legacy("N1")])
    assert env.client.post("/api/ui_notes/stage", json={"id": "N1", "to": "done"}).status_code == 422
    assert env.client.post("/api/ui_notes/stage", json={"id": "N404", "to": "doing"}).status_code == 404


def test_resolve_endpoint_is_stage_verified(env: Env) -> None:
    env.put([_legacy("N1")])
    res = env.client.post("/api/ui_notes/resolve", json={"id": "N1", "note": "处理完"})
    assert res.status_code == 200
    note = env.note("N1")
    assert note["stage"] == "verified" and note["status"] == "resolved"
    assert note["history"][-1]["to"] == "verified"
    assert note["history"][-1]["note"] == "处理完"


def test_update_text_does_not_touch_stage(env: Env) -> None:
    env.put([_legacy("N1", stage="fixed", history=[])])
    res = env.client.post("/api/ui_notes/update", json={"id": "N1", "text": "换个说法"})
    note = env.note("N1")
    assert res.status_code == 200
    assert note["text"] == "换个说法" and note["stage"] == "fixed"
    assert note["history"] == []