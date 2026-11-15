# -*- coding: utf-8 -*-
"""选股三端点 × **真实生产文件** 冒烟（可选，任务书 4.3 §3 末段）。

与 `test_compat_misc.py` 的区别：那边全部 tmp 造数（CI 可跑）；本文件读**磁盘上真实**的
`data/panel/selection_*.json`（只读，不写、不改），用来确认「白名单登记 + 透传拼装」
在真数据上不炸形状。文件缺失/未登记 → 自动 skip（CI/新机器不该因缺生产数据挂）。

约束（与插件同）：只 `ctx.panel_json` 只读，不查 `buy_signal.db`，不写文件。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import paths as core_paths  # noqa: E402
from app.plugins.compat_misc import backend  # noqa: E402

SEL_POOL_KEYS = {"date", "count", "is_current", "names", "items", "slots", "keep",
                "group_id", "mmdd"}   # 2026-11-13：池子改映射自选池的 MMDD 组，多两个键
SEL_FRESHNESS_KEYS = {"state", "snapshot_date", "expected_date", "produce_by",
                      "today_pending"}
# 盘后快照必需键（engine/collectors/selection_snapshot_builder.py::REQUIRED_KEYS 同集）
SEL_SNAP_REQUIRED = {"meta", "date", "asof", "filters", "filter_options", "group_sigs",
                     "group_filters", "board_map", "hot_concepts", "stats",
                     "warnings", "score_warn", "groups"}


class _RealCtx:
    """只实现 `panel_json` 的最小 ctx：按 core 白名单真读磁盘（不写、不连库）。"""

    id = "compat_misc"

    def __init__(self) -> None:
        self.calls: List[str] = []

    def panel_json(self, name: str) -> Dict[str, Any]:
        self.calls.append(name)
        path = core_paths.READABLE_PANEL.get(name)
        if path is None:
            raise PermissionError("%s 不在 READABLE_PANEL" % name)
        if not path.is_file():
            raise FileNotFoundError(str(path))
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("顶层必须是对象: %s" % path)
        return data


def _real_file(name: str) -> Path:
    path = core_paths.READABLE_PANEL.get(name)
    if path is None or not path.is_file():
        pytest.skip("真实生产文件缺失/未登记：%s（跳过，不依赖生产数据）" % name)
    return path


def test_selection_real_snapshot() -> None:
    """真实 8.2MB 快照：groups 非空、键集含旧端全部必需键、pool/freshness 拼得上。"""
    _real_file("selection_latest")
    body = backend.build_selection(_RealCtx())
    assert SEL_SNAP_REQUIRED <= set(body), sorted(SEL_SNAP_REQUIRED - set(body))
    assert isinstance(body["groups"], list) and body["groups"], "真实快照 groups 应为非空"
    assert isinstance(body["stats"], dict) and "hits" in body["stats"]
    # 有真数据时不该走缺快照分支
    assert "error" not in body
    # pool 段形状（真池文件可能没有 → 骨架，键仍然齐）
    assert set(body["pool"]) == SEL_POOL_KEYS
    # freshness 形状 + 值域（2026-11-04：盘中产物存在当日有效时走 intraday 分支，
    #   多出 mode/generated_at 两键；盘后分支仍是五键）
    mode = body.get("mode") or "postclose"
    assert mode in {"postclose", "intraday"}
    if mode == "intraday":
        assert set(body["freshness"]) == SEL_FRESHNESS_KEYS | {"mode", "generated_at"}
        assert body["freshness"]["mode"] == "intraday"
        assert body["freshness"]["state"] == "fresh"
        assert body["date"] == body["freshness"]["snapshot_date"]
    else:
        assert set(body["freshness"]) == SEL_FRESHNESS_KEYS
        assert body["freshness"]["state"] in {"fresh", "stale", "unknown"}
        assert body["freshness"]["snapshot_date"] == str(body.get("date") or "")
    assert body["freshness"]["produce_by"] == "18:00"


def test_selection_real_pool_and_prefs() -> None:
    """真实选股池 + 筛选偏好：两者都**整份透传**，条目/偏好一个键不加不减。"""
    _real_file("selection_pool")
    pool = backend.build_selection_pool(_RealCtx(), "")
    assert set(pool) == SEL_POOL_KEYS
    assert pool["keep"] == 3   # 2026-11-13：MMDD 日期分组最多留 3 个（主人定）
    assert pool["count"] == len(pool["items"])
    assert len(pool["names"]) <= 3
    assert isinstance(pool["is_current"], bool)
    if pool["date"]:
        # 2026-11-15：旧断言写死「只有当日一日」，但选股池正常态就是保留最近 keep 个收盘日
        #   （现在 11-12 与当日两组并存）——改成按契约断言：不超 keep、首项=当日、计数一致。
        assert 1 <= len(pool["slots"]) <= pool["keep"], pool["slots"]
        assert pool["slots"][0]["date"] == pool["date"]
        assert pool["slots"][0]["count"] == pool["count"]
    for item in pool["items"]:
        assert {"code", "name", "group", "sig", "added_at", "order"} <= set(item)

    prefs_path = core_paths.READABLE_PANEL.get("selection_filter")
    if prefs_path is None or not prefs_path.is_file():
        pytest.skip("selection_filter.json 缺失（跳过）")
    prefs = backend.build_selection_prefs(_RealCtx())
    assert isinstance(prefs, dict)
    # 守「透传原样」意图，**不硬编旧键集**（2026-10-25 修）。
    # 依据：`/api/selection_prefs` 的语义是**整份透传** selection_filter.json
    # （旧 8800 `api_selection_prefs` = `_read_json` 后原样 JSONResponse；插件
    # `build_selection_prefs` 逐字同义：读到 dict 就返回，缺失/损坏才 `{}`）。
    # 而写入口（旧 server.py:7004 `api_selection_prefs_save` 与本插件同名函数）是
    # **整份覆盖写**：`doc` 只有 `{meta}` + 前端本次显式传的字段。所以真实文件
    # 只剩 `{meta}` 是**正常数据态**（2026-10-25 11:33 前端保存过一次，只带了 meta），
    # 代码无 bug。此处按真实文件内容对齐断言：读到什么就透传什么，一个键不加不减。
    with prefs_path.open(encoding="utf-8") as fh:
        on_disk = json.load(fh)
    assert prefs == on_disk, "selection_prefs 必须是文件原样透传（不加不减不补默认值）"
    # 透传恒等式之外再锁两条不随数据态漂移的硬约束：
    #   ① meta 必在且是对象（写入口每次都写，缺失=写入口被绕过）；
    #   ② 若文件里带了旧端偏好键，必须是原值（不是被插件改写/补默认）。
    assert isinstance(prefs.get("meta"), dict), "selection_filter 必须带 meta（写入口产物）"
    for key in ("pools", "ma", "pin", "inds", "cons", "boards", "sigs", "boardOn", "hot"):
        if key in on_disk:
            assert prefs[key] == on_disk[key], key
