# -*- coding: utf-8 -*-
"""concept 插件 HTTP 契约测试（4.2）。

只读真实概念库做冒烟（文件稳定、只读）；另加临时空目录验证缺失降级。
不写任何生产文件。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import paths  # noqa: E402
from app.core.main import app  # noqa: E402


def test_concept_index_real() -> None:
    body = TestClient(app).get("/api/concept.json").json()
    assert set(body) == {"ok", "updated", "active", "watch", "chain", "archived",
                         "counts", "sector_map", "unresolved_targets", "sector_unresolved"}
    assert body["ok"] is True
    assert body["counts"]["active"] >= 1
    assert isinstance(body["active"], list)


def test_chain_detail_outer_tokens_are_never_stock() -> None:
    """链位：括号外一律 label（主人 2026-11-14 定）。

    回归背景：`新能源车`(399417.SZ) / `数据要素`(399418.SZ) / `机器人`(300024.SZ) 这些
    **行业词/指数别名**原本只为概况正文 linkify 而存在，接到链位后变成了 5 个假链接。
    """
    body = TestClient(app).get("/api/concept.json").json()
    assert body["ok"] is True
    for c in body["chain"]:
        for key in ("up_detail", "mid_detail", "down_detail"):
            for it in c[key]:
                assert set(it) == {"name", "code", "kind", "in_group"}, it
                if not it["in_group"]:
                    assert it["kind"] == "label" and it["code"] == "", (c["name"], key, it)


def test_concept_detail_real() -> None:
    idx = TestClient(app).get("/api/concept.json").json()
    slug = next((a.get("slug") for a in idx.get("active") or [] if a.get("slug")), None)
    if not slug:
        pytest.skip("真实概念库没有带 slug 的活跃概念")
    res = TestClient(app).get("/api/concept.json", params={"slug": slug})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True and body["slug"] == slug
    assert "<" in body["html"]


def test_concept_detail_missing_404() -> None:
    assert TestClient(app).get("/api/concept.json", params={"slug": "__no_such__"}).status_code == 404


def test_concept_missing_master_degrades(tmp_path: Path, monkeypatch) -> None:
    empty = tmp_path / "concept_empty"
    empty.mkdir()
    monkeypatch.setitem(paths.READABLE_TEXT_DIR, "concept_tracking", empty)
    body = TestClient(app).get("/api/concept.json").json()
    assert body.get("ok") is False
    assert "概念与催化总表.md" in (body.get("error") or "")


def test_name_alias_covers_exright_temporary_name() -> None:
    """东财名表除权期把 601696 写成「XD中银证」→ 永久名「中银证券」仍须解析到个股。

    回归背景（2026-11-07）：旧 TDX 名表能链「中银证券」，新 stock_names 当日只有
    除权临时名，导致真实个股漏链。别名表负责补「东财名表缺、TDX 有」的永久名。
    """
    from app.plugins.concept import backend

    class _Ctx:
        def json_read(self, name):
            assert name == "stock_names"
            return {"names": {"601696": "XD中银证"}}

    names = backend._name_to_code_map(_Ctx())
    resolve = backend._make_name_to_code(names)
    assert resolve("中银证券") == "601696.SH"
