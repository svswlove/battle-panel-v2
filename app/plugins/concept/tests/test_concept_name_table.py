# -*- coding: utf-8 -*-
"""concept 插件 · 名表解析（除权/新股临时名）单测（2026-11-14）

背景：主人实测活接口发现 `盛和资源`（稀土永磁·上游括号内）是灰 chip——
`data/stock_names.json` 今日把 600392 写成「XD盛和资」（XD 除权前缀 + 沪市简称截到 3 字）。
今天漏盛和资源，明天就漏别的，故按系统性口径处理（方案 b）：

  1. 名表构建时，对 `XD/XR/DR（除权除息）+ N（首日）/ C（上市前 5 日）` 前缀的条目，
     额外登记**表面名**（剥前缀后的名字，长 ≥3）；
  2. 查询侧精确名未命中时，按「查询名以表面名开头（含相等）」兜底，**且必须唯一命中**；
  3. 表面名撞车（两个码）→ 剔除，宁可不 link 也不误配；非临时名（如「数字人」）不参与前缀匹配。

`_NAME_CODE_ALIASES` 不动：概念概况正文还在用（创业板指/数据要素是合法的指数链），
本文件也守住「别名照常生效」这条。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.plugins.concept import backend  # noqa: E402


def _ctx(payload):
    """假 ctx：只实现 `json_read("stock_names")`。"""
    class _Ctx:
        def json_read(self, name):
            assert name == backend.NAMES_JSON
            return payload

    return _Ctx()


def _resolve(payload):
    table = backend._name_table(_ctx(payload))
    return table, backend._make_name_to_code(table["names"], table["surface"])


def test_exright_name_resolves_permanent_name() -> None:
    """`XD盛和资` → 永久名「盛和资源」也要能解析（本轮的核心回归）。"""
    _, resolve = _resolve({"names": {"600392": "XD盛和资"}})
    assert resolve("盛和资源") == "600392.SH"
    assert resolve("XD盛和资") == "600392.SH"      # 表内原名照旧可用
    assert resolve("盛和资") == "600392.SH"        # 表面名本身也可用


def test_exright_untruncated_name_resolves() -> None:
    """未截断的临时名（XD吉比特）→ 写「吉比特」也要解析。"""
    _, resolve = _resolve({"names": {"603444": "XD吉比特", "601128": "XD常熟银"}})
    assert resolve("吉比特") == "603444.SH"
    assert resolve("常熟银行") == "601128.SH"


def test_new_stock_prefixes() -> None:
    """N（首日）/ C（上市前 5 日）同样剥前缀兜底。"""
    _, resolve = _resolve({"names": {"603123": "N某某科技", "688999": "C某某股份"}})
    assert resolve("某某科技") == "603123.SH"
    assert resolve("某某股份") == "688999.SH"


def test_permanent_name_is_not_prefix_matched() -> None:
    """非临时名不参与前缀匹配：「数字人民币」不得被链到「数字人」(920670.BJ)。"""
    _, resolve = _resolve({"names": {"920670": "数字人", "300075": "数字政通"}})
    assert resolve("数字人民币") == ""
    assert resolve("数字政通") == "300075.SZ"


def test_surface_ambiguity_is_rejected() -> None:
    """两个码共用一个表面名 → 剔除，不猜。"""
    payload = {"names": {"600001": "XD盛和资", "600002": "XD盛和资"}}
    table, resolve = _resolve(payload)
    assert table["surface"] == {}
    assert resolve("盛和资源") == ""


def test_short_surface_name_excluded() -> None:
    """表面名短于 3 字不参与兜底（避免撞常用词）。"""
    table, resolve = _resolve({"names": {"688827": "C钶锐"}})
    assert table["surface"] == {}
    assert resolve("钶锐锶") == ""


def test_aliases_still_work() -> None:
    """`_NAME_CODE_ALIASES` 未删未改：正文链路（创业板指/数据要素/中银证券）照旧。"""
    table, resolve = _resolve({"names": {"601696": "XD中银证"}})
    assert resolve("中银证券") == "601696.SH"
    assert resolve("数据要素") == "399418.SZ"
    assert resolve("创业板指") == "399006.SZ"
    assert table["names"]["创业板"] == "395004.SZ"


def test_without_surface_layer_keeps_exact_only() -> None:
    """旧调用形态（只传 names）行为不变：精确匹配，不做前缀兜底。"""
    names = backend._name_to_code_map(_ctx({"names": {"600392": "XD盛和资"}}))
    resolve = backend._make_name_to_code(names)
    assert resolve("XD盛和资") == "600392.SH"
    assert resolve("盛和资源") == ""


def test_detail_linkify_keeps_exact_only(tmp_path: Path) -> None:
    """概况正文（滑窗 linkify）不得吃前缀兜底。

    实测事故备份（2026-11-14 本机）：把带 surface 的 resolver 传给 `render.md_to_html`，
    「盛和资源与数字人」被整段包成一个 chip（窗口 8 字，正好以表面名开头）。
    口径：兜底只接**离散 token**（链位/核心标的）；正文滑窗只能用精确名表。
    """
    concept_dir = tmp_path / "concept"
    (concept_dir / "甲").mkdir(parents=True)
    (concept_dir / backend.source.MASTER_NAME).write_text("# 总表\n", encoding="utf-8")
    (concept_dir / "甲" / backend.source.DETAIL_NAME).write_text(
        "测试：盛和资源与数字人民币并列。\n", encoding="utf-8")

    class _Ctx:
        def json_read(self, _name):
            return {"names": {"600392": "XD盛和资", "920670": "数字人"}}

        def text_dir(self, _name):
            return concept_dir

    html = backend.build_concept_detail(_Ctx(), "甲")["html"]
    assert "盛和资源" in html, "正文不得丢字"
    assert 'data-open-name="盛和资源' not in html, "正文滑窗不得用前缀兜底（会吞掉后文）"
    assert 'data-open-kline="920670.BJ"' in html, "精确名照旧可链（旧行为不变）"


def test_missing_table_is_safe() -> None:
    """名表缺失/坏 → 空表 + 解析空串（不抛穿）。"""
    class _Bad:
        def json_read(self, _name):
            raise RuntimeError("boom")

    table = backend._name_table(_Bad())
    assert table["surface"] == {}                              # 表坏 → 无表面名兜底
    assert table["names"] == backend._NAME_CODE_ALIASES        # 只剩显式别名（旧口径不变）
    assert backend._make_name_to_code(table["names"], table["surface"])("盛和资源") == ""
