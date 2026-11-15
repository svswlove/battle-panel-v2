# -*- coding: utf-8 -*-
"""插件落盘缓存（ctx.cache_read / ctx.cache_write）契约测试。

2026-11-07：盘后结果落盘（主人：「收盘后的数据就已经固定下来了，没必要每次都重拉重算」）。
core 只提供存储与原子写；版本失效由插件自己决定（把数据戳写进 payload）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import paths  # noqa: E402
from app.core.contract import load_plugin  # noqa: E402

PLUGINS = ROOT / "app" / "plugins"


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "OUT_DIR", tmp_path / "out")
    return load_plugin(PLUGINS / "compat_watchlist").ctx


def test_write_then_read_roundtrip(ctx, tmp_path) -> None:
    payload = {"version": "abc", "items": {"a": 1, "b": [1, 2, 3]}}
    path = ctx.cache_write("demo", payload)
    assert path.is_file()
    assert path.parent.name == "compat_watchlist"
    assert path.parent.parent.name == "_cache"
    assert ctx.cache_read("demo") == payload


def test_missing_file_raises_file_not_found(ctx) -> None:
    with pytest.raises(FileNotFoundError):
        ctx.cache_read("never_written")


def test_corrupt_file_raises_value_error(ctx) -> None:
    path = paths.plugin_cache_path(ctx.id, "broken")
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(Exception):
        ctx.cache_read("broken")
    path.write_text('["not", "dict"]', encoding="utf-8")
    with pytest.raises(ValueError):
        ctx.cache_read("broken")


def test_path_traversal_rejected(ctx) -> None:
    for bad in ("../x", "a/b", "..", "", "x" * 100):
        with pytest.raises(ValueError):
            ctx.cache_write(bad, {"v": 1})


def test_cache_is_per_plugin(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(paths, "OUT_DIR", tmp_path / "out")
    a = load_plugin(PLUGINS / "compat_watchlist").ctx
    b = load_plugin(PLUGINS / "compat_kline").ctx
    a.cache_write("shared_name", {"who": "a"})
    with pytest.raises(FileNotFoundError):
        b.cache_read("shared_name")


def test_write_is_atomic_and_leaves_no_tmp(ctx) -> None:
    ctx.cache_write("atomic", {"n": 1})
    ctx.cache_write("atomic", {"n": 2})
    assert ctx.cache_read("atomic")["n"] == 2
    leftovers = list(paths.plugin_cache_path(ctx.id, "atomic").parent.glob("*.tmp"))
    assert leftovers == []


def test_payload_must_be_dict(ctx) -> None:
    with pytest.raises(ValueError):
        ctx.cache_write("list", [1, 2, 3])


def test_output_read_other_plugin(ctx, tmp_path) -> None:
    """跨插件只读：产品归生产插件，消费方不算第二遍。"""
    out = paths.OUT_DIR / "thermometer"
    out.mkdir(parents=True, exist_ok=True)
    (out / "index_880008_series.json").write_text(
        json.dumps({"merged_last": "20261106", "bars": [1, 2, 3]}), encoding="utf-8")
    got = ctx.output_read("thermometer", "index_880008_series")
    assert got["merged_last"] == "20261106" and got["bars"] == [1, 2, 3]


def test_output_read_missing_returns_default(ctx) -> None:
    assert ctx.output_read("thermometer", "never_there", default={"x": 1}) == {"x": 1}


def test_output_read_rejects_bad_names(ctx) -> None:
    for bad in ("../etc", "a/b", ".."):
        with pytest.raises(ValueError):
            ctx.output_read(bad, "x")
