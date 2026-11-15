# -*- coding: utf-8 -*-
"""concept/source.py 单测（2026-10-23 从旧仓同名单测移植）

覆盖（旧断言全保留）：
  · 总表三段解析：活跃概念 / 观察池(AMV口径) / 产业链链位映射 / 已归档
  · 温度带判定：主升浪→hot、催化进行中·高位放量分歧→warm、主线降温→cool
  · 阶段短串：剥离「（注记）」括号尾巴
  · 表头抗换列：列序调换/改名后仍按表头取值（2026-10-01 观察池换口径的防再犯）
  · slug 抽取与路径安全：链接取目录名；详情禁路径穿越 / 缺失 → ok:False
  · 文件缺失 → ok:False 不抛穿
  · 活跃概念 → THS 板块三级名字规则（精确 / 加概念 / 唯一包含；歧义放弃）
  · 核心标的 名→码（targets_detail + unresolved_targets）

迁移适配（任务书 docs/task_concept_4_2.md §4.1）：
  · 概念目录一律用 `tmp_path` 合成样本，**绝不读真实** 夏天-activity/概念追踪；
  · `sector_names` 直接传 dict（不再 monkeypatch sqlite / 不建临时库）；
  · `name_to_code` 传固定映射（不再 mock `common.names.code_of`）；
  · 中文注释保留；本文件不产生任何真实 IO 之外的写入（只写 tmp_path）。

注意：本文件**未运行验证**（交付环境无 shell）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.plugins.concept import source as cs  # noqa: E402

MASTER = """# 概念与催化总表

> 最后更新：2026-10-01（测试样本）

## 活跃概念（有明确催化 + 盘面响应）

| 概念 | 当前阶段 | 逻辑类型 | 持续性 | 最近更新 | 下一个验证节点 | 核心标的 |
|------|---------|---------|-------|---------|--------------|---------|
| [甲概念](./甲概念/概况.md) | **主升浪·高位天量分歧** | 产业事实确认 | 结构性 | 2026-08-09 | 大单履行 | 云南锗业/源杰科技 |
| [乙概念](./乙概念/概况.md) | 催化进行中·高位放量分歧（天孚+44%） | 产业升级 | 2-3年 | 2026-08-09 | 中报 | 光库科技 |
| [丙概念](./丙概念/概况.md) | **主线降温·内部分化** | 业绩验证 | 防守 | 2026-08-12 | 药明 | 药明康德 |

## 观察池（AMV 主线口径）

| 方向 | AMV 参照板块（THS） | AMV 多空（09-30） | 待验证问题 |
|------|-------------------|------------------|----------|
| 碳纤维 | 碳纤维 885650.TI | 空（+0.00004） | 单票还是板块 |
| 人形机器人 | 人形机器人 886069.TI | 多（+0.001） | 承接验证 |

## 产业链链位映射（异动归因用）

| 概念 | 上游 | 本环节 | 下游 |
|------|------|--------|------|
| 甲概念 | 锗/铟原料 | 磷化铟衬底 | 光模块/CPO |

## 已归档（催化结束/轮动退潮）

（暂无）
"""

# 列序调换 + 改名：验证「按表头取值」抗换列（活跃概念）
MASTER_REORDERED = """# 概念与催化总表

> 最后更新：2026-10-01

## 活跃概念

| 当前阶段 | 概念 | 核心标的 | 逻辑类型 | 持续性 | 最近更新 | 下一个验证节点 |
|---------|------|---------|---------|-------|---------|--------------|
| 主升浪·放量 | [甲概念](./甲概念/概况.md) | A股/B股 | 事实 | 年 | 2026-01-01 | 节点 |
"""

DETAIL = "# 甲概念 — 概念概况\n\n## 1. 定位\n\n- 位置：上游→下游\n"

# 观察池结构化字段样本（多板块 + 多权重变化 + U+2212 负号）
MASTER_WATCH_STRUCT = """# 概念与催化总表

> 最后更新：2026-10-02

## 观察池（AMV 主线口径）

| 方向 | AMV 参照板块（THS） | AMV 多空（09-30） | 待验证问题 |
|------|-------------------|------------------|----------|
| 半导体设备/靶材 | 半导体设备 884229.TI / 半导体材料 884091.TI | 空（−0.00103 / +0.00058） | 靶材是否独立激活 |
| 无代码方向 | 某板块 | 空 | 问 |
"""

# 活跃概念 → THS 板块（2026-10-02，主人报「活跃池跳不了板块详情」）
MASTER_SECTOR = """# 概念与催化总表

> 最后更新：2026-10-02

## 活跃概念

| 概念 | 当前阶段 | 逻辑类型 | 持续性 | 最近更新 | 下一个验证节点 | 核心标的 |
|------|---------|---------|-------|---------|--------------|---------|
| [甲概念](./甲概念/概况.md) | 主升浪 | 事实 | 长 | 2026-01-01 | 节点 | A股 |
| [乙XX/丙Y](./乙XX/概况.md) | 主升浪 | 事实 | 长 | 2026-01-01 | 节点 | A股 |
| [丁ZZ·戊W](./丁ZZ/概况.md) | 主升浪 | 事实 | 长 | 2026-01-01 | 节点 | A股 |
| [己概念](./己/概况.md) | 主升浪 | 事实 | 长 | 2026-01-01 | 节点 | A股 |
| [辛概念](./辛/概况.md) | 主升浪 | 事实 | 长 | 2026-01-01 | 节点 | A股 |
| [庚概念](./庚/概况.md) | 主升浪 | 事实 | 长 | 2026-01-01 | 节点 | A股 |
"""

# 合成板块名表（等价旧测试的 tmp ths_board.db：concept 表优先、board 表补）
SECTOR_NAMES = {
    "甲概念": "880001.TI",       # concept 精确
    "丙Y概念": "880002.TI",      # concept，加「概念」后缀命中
    "戊W延伸版": "880003.TI",    # concept，唯一包含命中
    "己概念A": "880004.TI",      # 与下面一条同时包含「己概念」→ 歧义
    "己概念B": "880005.TI",
    "辛概念": "881001.TI",       # board（行业表）也参与反查
}


# ---------------------------------------------------------------- 固件


@pytest.fixture()
def concept_dir(tmp_path: Path) -> Path:
    """临时概念目录 + 总表样本（缓存每次清空，避免跨用例串味）。"""
    cs.reset_cache()
    (tmp_path / cs.MASTER_NAME).write_text(MASTER, encoding="utf-8")
    yield tmp_path
    cs.reset_cache()


def _write_master(root: Path, text: str) -> None:
    (root / cs.MASTER_NAME).write_text(text, encoding="utf-8")
    cs.reset_cache()


# ---------------------------------------------------------------- 总表索引


def test_active_parsed(concept_dir: Path) -> None:
    d = cs.load_index(concept_dir)
    assert d["ok"] is True
    assert d["updated"] == "2026-10-01"
    assert d["counts"]["active"] == 3
    names = [a["name"] for a in d["active"]]
    assert names == ["甲概念", "乙概念", "丙概念"]
    assert d["active"][0]["slug"] == "甲概念"
    assert d["active"][0]["targets"] == ["云南锗业", "源杰科技"]


def test_heat_classification(concept_dir: Path) -> None:
    d = cs.load_index(concept_dir)
    heat = {a["name"]: a["heat"] for a in d["active"]}
    assert heat["甲概念"] == "hot"      # 主升浪：副段分歧不降级
    assert heat["乙概念"] == "warm"     # 催化进行中·高位放量分歧 → 降一档
    assert heat["丙概念"] == "cool"     # 主线降温
    assert d["counts"]["hot"] == 1
    assert d["counts"]["warm"] == 1
    assert d["counts"]["cool"] == 1


def test_stage_short_strips_note(concept_dir: Path) -> None:
    d = cs.load_index(concept_dir)
    by = {a["name"]: a for a in d["active"]}
    assert by["乙概念"]["stage_short"] == "催化进行中·高位放量分歧"
    assert by["丙概念"]["stage_short"] == "主线降温·内部分化"


def test_watch_amv_caliber(concept_dir: Path) -> None:
    d = cs.load_index(concept_dir)
    assert d["counts"]["watch"] == 2
    by = {w["name"]: w for w in d["watch"]}
    assert by["碳纤维"]["codes"] == ["885650.TI"]
    assert by["碳纤维"]["kind"] == "bear"
    assert by["人形机器人"]["kind"] == "bull"
    assert by["碳纤维"]["question"] == "单票还是板块"


def test_chain_parsed(concept_dir: Path) -> None:
    d = cs.load_index(concept_dir)
    assert d["counts"]["chain"] == 1
    c = d["chain"][0]
    assert (c["name"], c["up"], c["mid"], c["down"]) == \
        ("甲概念", "锗/铟原料", "磷化铟衬底", "光模块/CPO")


def test_sector_map(concept_dir: Path) -> None:
    d = cs.load_index(concept_dir)
    assert d["sector_map"]["codes"]["885650.TI"] == "碳纤维"
    assert "甲概念" in d["sector_map"]["names"]


def test_header_reorder_tolerant(concept_dir: Path) -> None:
    _write_master(concept_dir, MASTER_REORDERED)
    d = cs.load_index(concept_dir)
    a = d["active"][0]
    assert a["name"] == "甲概念"
    assert a["targets"] == ["A股", "B股"]
    assert a["logic"] == "事实"
    assert a["next_node"] == "节点"


def test_index_keys_match_legacy_contract(concept_dir: Path) -> None:
    """顶层 key 集合与旧端一致（compare_legacy 结构比对的前提）。"""
    d = cs.load_index(concept_dir)
    assert set(d) == {"ok", "updated", "active", "watch", "chain", "archived",
                      "counts", "sector_map", "unresolved_targets", "sector_unresolved"}
    assert set(d["counts"]) == {"active", "watch", "chain", "archived",
                                "hot", "warm", "cool"}


# ---------------------------------------------------------------- 核心标的 名→码
# 2026-10-02：targets_detail 供前端点开 K 线，旧 targets 保留（只加不改名）


def test_targets_detail_with_stub_resolver(concept_dir: Path) -> None:
    codes = {"云南锗业": "002428.SZ", "源杰科技": "688498.SH"}
    d = cs.load_index(concept_dir, name_to_code=lambda n: codes.get(n))
    a = {x["name"]: x for x in d["active"]}["甲概念"]
    assert a["targets_detail"] == [
        {"name": "云南锗业", "code": "002428.SZ"},
        {"name": "源杰科技", "code": "688498.SH"},
    ]
    assert a["targets"] == ["云南锗业", "源杰科技"]   # 旧字段保留


def test_unresolved_targets_reported(concept_dir: Path) -> None:
    """解析不到的标的名要**可见**（不静默变成「点了没反应」）。"""
    d = cs.load_index(concept_dir, name_to_code=lambda n: None)
    a = {x["name"]: x for x in d["active"]}["甲概念"]
    assert [t["code"] for t in a["targets_detail"]] == ["", ""]
    assert "云南锗业" in d["unresolved_targets"]


def test_targets_detail_without_resolver_is_empty_code(concept_dir: Path) -> None:
    """未注入解析器（= 名表不可用）→ code 全空，不猜、不崩。"""
    d = cs.load_index(concept_dir)
    a = {x["name"]: x for x in d["active"]}["甲概念"]
    assert [t["code"] for t in a["targets_detail"]] == ["", ""]
    assert "云南锗业" in d["unresolved_targets"]


def test_targets_detail_resolver_exception_is_safe(concept_dir: Path) -> None:
    """解析器抛错 → code 置空，不影响概念解析主流程（不 500）。"""
    def boom(name: str):
        raise RuntimeError("boom")

    d = cs.load_index(concept_dir, name_to_code=boom)
    assert d["ok"] is True
    a = {x["name"]: x for x in d["active"]}["甲概念"]
    assert all(t["code"] == "" for t in a["targets_detail"])


# ---------------------------------------------------------------- 观察池结构化字段


def test_sectors_list_and_deltas_single(concept_dir: Path) -> None:
    d = cs.load_index(concept_dir)
    by = {w["name"]: w for w in d["watch"]}
    assert by["碳纤维"]["sectors_list"] == [{"name": "碳纤维", "code": "885650.TI"}]
    assert by["碳纤维"]["deltas"] == [0.00004]
    assert by["人形机器人"]["deltas"] == [0.001]


def test_sectors_list_multi_and_unicode_minus(concept_dir: Path) -> None:
    _write_master(concept_dir, MASTER_WATCH_STRUCT)
    d = cs.load_index(concept_dir)
    by = {w["name"]: w for w in d["watch"]}
    assert by["半导体设备/靶材"]["sectors_list"] == [
        {"name": "半导体设备", "code": "884229.TI"},
        {"name": "半导体材料", "code": "884091.TI"},
    ]
    assert by["半导体设备/靶材"]["deltas"] == [-0.00103, 0.00058]
    assert by["无代码方向"]["sectors_list"] == [{"name": "某板块", "code": ""}]
    assert by["无代码方向"]["deltas"] == []   # 取不到数字不编造


def test_legacy_fields_kept(concept_dir: Path) -> None:
    """契约只加不改名：旧字段仍在（前端/深链旧路径不破）。"""
    d = cs.load_index(concept_dir)
    by = {w["name"]: w for w in d["watch"]}
    for k in ("name", "sectors", "codes", "amv", "kind", "question"):
        assert k in by["碳纤维"]


# ---------------------------------------------------------------- 文件缺失


def test_master_missing(concept_dir: Path) -> None:
    (concept_dir / cs.MASTER_NAME).unlink()
    cs.reset_cache()
    d = cs.load_index(concept_dir)
    assert d["ok"] is False
    assert "缺失" in d["error"]


def test_master_missing_dir_entirely(tmp_path: Path) -> None:
    """目录不存在也不抛穿（端点层据 ok:False 降级）。"""
    cs.reset_cache()
    d = cs.load_index(tmp_path / "nope")
    assert d["ok"] is False
    assert "缺失" in d["error"]


# ---------------------------------------------------------------- 概况详情


@pytest.fixture()
def detail_dir(concept_dir: Path) -> Path:
    folder = concept_dir / "甲概念"
    folder.mkdir(exist_ok=True)
    (folder / cs.DETAIL_NAME).write_text(DETAIL, encoding="utf-8")
    return concept_dir


def test_detail_ok(detail_dir: Path) -> None:
    r = cs.load_detail("甲概念", detail_dir)
    assert r["ok"] is True
    assert r["slug"] == "甲概念"
    assert "定位" in r["md"]


@pytest.mark.parametrize("bad", ["../甲概念", "..\\甲概念", "a/b", "..", "", None, "甲概念/../.."])
def test_detail_traversal_blocked(detail_dir: Path, bad) -> None:
    r = cs.load_detail(bad, detail_dir)
    assert r["ok"] is False, "应拒绝非法 slug: %r" % (bad,)


def test_detail_missing(detail_dir: Path) -> None:
    r = cs.load_detail("不存在", detail_dir)
    assert r["ok"] is False
    assert "缺失" in r["error"]


# ---------------------------------------------------------------- 活跃概念 → THS 板块
# 2026-10-02：纯名字三级规则，命中不了留空不编造；名字表由调用方注入（不再连库）


@pytest.fixture()
def sector_dir(concept_dir: Path) -> Path:
    _write_master(concept_dir, MASTER_SECTOR)
    return concept_dir


def _by_name(root: Path):
    d = cs.load_index(root, sector_names=SECTOR_NAMES)
    return d, {a["name"]: a for a in d["active"]}


def test_exact_name(sector_dir: Path) -> None:
    _, by = _by_name(sector_dir)
    assert by["甲概念"]["sectors_list"] == [
        {"name": "甲概念", "code": "880001.TI", "src": "精确"}]


def test_suffix_concept(sector_dir: Path) -> None:
    """THS 概念表用「PCB概念」这类写法 → 允许加「概念」后缀命中。"""
    _, by = _by_name(sector_dir)
    assert by["乙XX/丙Y"]["sectors_list"] == [
        {"name": "丙Y概念", "code": "880002.TI", "src": "加概念"}]


def test_unique_containment(sector_dir: Path) -> None:
    """如 CPO ⊂ 共封装光学(CPO)：被唯一一个板块名包含才认。"""
    _, by = _by_name(sector_dir)
    assert by["丁ZZ·戊W"]["sectors_list"] == [
        {"name": "戊W延伸版", "code": "880003.TI", "src": "唯一包含"}]


def test_board_table_searched(sector_dir: Path) -> None:
    """行业表（board）同样参与反查，不只是概念表（此处名字由调用方合并后传入）。"""
    _, by = _by_name(sector_dir)
    assert by["辛概念"]["sectors_list"][0]["code"] == "881001.TI"


def test_ambiguous_containment_rejected(sector_dir: Path) -> None:
    """己概念 被两个板块名包含 → 放弃，不猜。"""
    _, by = _by_name(sector_dir)
    assert by["己概念"]["sectors_list"] == []


def test_unresolved_reported(sector_dir: Path) -> None:
    d, _ = _by_name(sector_dir)
    assert "己概念" in d["sector_unresolved"]
    assert "庚概念" in d["sector_unresolved"]
    assert "甲概念" not in d["sector_unresolved"]


def test_missing_sector_names_is_safe(sector_dir: Path) -> None:
    """板块名表不可用（未注入/空）→ 全部留空且不崩（概念解析主流程不受影响）。"""
    cs.reset_cache()
    d = cs.load_index(sector_dir)
    assert d["ok"] is True
    by = {a["name"]: a for a in d["active"]}
    assert by["甲概念"]["sectors_list"] == []
    assert len(d["sector_unresolved"]) == 6


# ---------------------------------------------------------------- 缓存与注入


def test_cache_keyed_by_concept_dir(tmp_path: Path) -> None:
    """缓存指纹含概念目录：两个不同目录互不串味（任务书 §3.2 要求）。"""
    cs.reset_cache()
    one = tmp_path / "one"
    two = tmp_path / "two"
    one.mkdir()
    two.mkdir()
    _write_master(one, MASTER)
    _write_master(two, MASTER_WATCH_STRUCT)
    d1 = cs.load_index(one)
    d2 = cs.load_index(two)
    assert d1["counts"]["active"] == 3
    assert d2["counts"]["active"] == 0
    assert d2["counts"]["watch"] == 2


def test_source_has_no_db_or_common_import() -> None:
    """本模块不得 import sqlite3 / common / xtquant / eltdx（插件静态扫描红线）。

    用 AST 取真实 import（注释与文档串里提到这些词不算违规——文档正需要说明为什么禁）。
    """
    import ast

    src = Path(cs.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mods.add(node.module.split(".")[0])
    for banned in ("sqlite3", "common", "xtquant", "xtdata", "eltdx", "core"):
        assert banned not in mods, "source.py 不得 import %s（实际: %s）" % (banned, sorted(mods))
