# -*- coding: utf-8 -*-
"""concept/source.py 链位单元拆分单测（2026-11-14）

背景：主人报「带括号的链位单元（`类别A/类别B（标的1/标的2）`）被 `/` 盲切，
名称解析不到代码 → 灰 chip 点不动」。

口径（本文件即验收）：
  · 括号外层按 `/` `+` `→` `、` 拆；括号内层按 `/` `、`（另含 `，` `；` `;` 把注记断开）
  · 段内首个 `：`/`=` 左侧 = 类别（kind=label），右侧按标的解析
  · stock/label **只由 name_to_code 判定**：解析出码 = stock；解析不出 = label
    （不认关键词表，不拿「未上市」「待补」之类词去猜）
  · 括号内 token 带 in_group=True；旧 up/mid/down 原文保留不改名不改义

样本全部是 `夏天-activity/概念追踪/概念与催化总表.md` 的**真实原文**
（本文件只读这些字符串常量，不读真实文件、不写任何生产文件）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.plugins.concept import source as cs  # noqa: E402

PLUGIN_DIR = Path(__file__).resolve().parents[1]

# 名→码替身：键取自真实 `data/stock_names.json`（只挑样本里出现的）；未列出的名字一律解析不到
CODES = {
    "茂莱光学": "688502.SH",
    "海立股份": "600619.SH",
    "张江高科": "600895.SH",
    "长鑫科技": "688825.SH",
    "兆易创新": "603986.SH",
    "德明利": "001309.SZ",
    "通富微电": "002156.SZ",
    "长电科技": "600584.SH",
    "赛力斯": "601127.SH",
    "江淮汽车": "600418.SH",
}
resolve = lambda n: CODES.get(n)  # noqa: E731 - 与生产注入的 callable 同形态


# ---------------------------------------------------------------- 8 条真实原文


# (原文, [(name, kind), ...]) —— 顺序即原文顺序
CASES = [
    (
        "光学系统/光源/温控部件（茂莱光学/海立股份）",
        [("光学系统", "label"), ("光源", "label"), ("温控部件", "label"),
         ("茂莱光学", "stock"), ("海立股份", "stock")],
    ),
    (
        "整机（上海微电子/御量升，未上市；A股映射=张江高科参股）",
        [("整机", "label"), ("上海微电子", "label"), ("御量升", "label"),
         ("未上市", "label"), ("A股映射", "label"), ("张江高科参股", "stock")],
    ),
    (
        "晶圆制造（中芯/华虹/长鑫）+ 配套材料（光刻胶/特气）",
        [("晶圆制造", "label"), ("中芯", "label"), ("华虹", "label"), ("长鑫", "label"),
         ("配套材料", "label"), ("光刻胶", "label"), ("特气", "label")],
    ),
    (
        "封测（通富微电/长电科技）→ 终端应用",
        [("封测", "label"), ("通富微电", "stock"), ("长电科技", "stock"),
         ("终端应用", "label")],
    ),
    (
        "DRAM/NAND 制造 + 利基设计 + 模组（长鑫科技/兆易创新/德明利）",
        [("DRAM/NAND 制造", "label"), ("利基设计", "label"), ("模组", "label"),
         ("长鑫科技", "stock"), ("兆易创新", "stock"), ("德明利", "stock")],
    ),
    (
        "气体原料/空分（待补）",
        [("气体原料", "label"), ("空分", "label"), ("待补", "label")],
    ),
    (
        "整车（华为智选/鸿蒙智行：赛力斯/江淮汽车）",
        [("整车", "label"), ("华为智选", "label"), ("鸿蒙智行", "label"),
         ("赛力斯", "stock"), ("江淮汽车", "stock")],
    ),
    (
        "锗/铟原料",
        [("锗", "label"), ("铟原料", "label")],
    ),
]


@pytest.mark.parametrize("cell,expected", CASES, ids=[c[0][:14] for c in CASES])
def test_chain_detail_real_cells(cell: str, expected) -> None:
    """8 条真实原文 → 有序 items，名称/类型逐项对齐。"""
    items = cs._chain_detail(cell, resolve)
    assert [(i["name"], i["kind"]) for i in items] == expected
    # kind 与 code 必须自洽：stock 有码、label 无码（前端据此决定可点/灰）
    for i in items:
        assert (i["kind"] == "stock") == bool(i["code"]), i
        assert set(i) == {"name", "code", "kind", "in_group"}


def test_case_counts_match_task_book() -> None:
    """任务书点名的计数逐条对齐（防口径漂移）。"""
    def stats(cell):
        items = cs._chain_detail(cell, resolve)
        return (len(items),
                sum(1 for i in items if i["kind"] == "label"),
                sum(1 for i in items if i["kind"] == "stock"))

    assert stats("光学系统/光源/温控部件（茂莱光学/海立股份）") == (5, 3, 2)
    assert stats("晶圆制造（中芯/华虹/长鑫）+ 配套材料（光刻胶/特气）") == (7, 7, 0)
    assert stats("封测（通富微电/长电科技）→ 终端应用") == (4, 2, 2)
    assert stats("DRAM/NAND 制造 + 利基设计 + 模组（长鑫科技/兆易创新/德明利）") == (6, 3, 3)
    assert stats("气体原料/空分（待补）") == (3, 3, 0)
    assert stats("锗/铟原料") == (2, 2, 0)


def test_unlisted_words_never_become_stock() -> None:
    """`未上市` / `待补` 这类词只能是 label——解析器解析不到就是不猜。"""
    names = {i["name"]: i for i in cs._chain_detail(
        "整机（上海微电子/御量升，未上市；A股映射=张江高科参股）", resolve)}
    assert names["未上市"]["kind"] == "label" and names["未上市"]["code"] == ""
    assert names["上海微电子"]["kind"] == "label"
    assert names["御量升"]["kind"] == "label"
    # 张江高科参股：整词解析不到 → 最长可解析前缀 张江高科，name 保留原文（不改写事实源）
    assert names["张江高科参股"]["kind"] == "stock"
    assert names["张江高科参股"]["code"] == "600895.SH"

    pending = cs._chain_detail("气体原料/空分（待补）", resolve)
    assert [i["kind"] for i in pending if i["name"] == "待补"] == ["label"]


def test_group_flag_marks_bracket_content() -> None:
    """括号内 token 带 in_group；括号字符不进 chip 文本。"""
    items = cs._chain_detail("晶圆制造（中芯/华虹/长鑫）+ 配套材料（光刻胶/特气）", resolve)
    flags = {i["name"]: i["in_group"] for i in items}
    assert flags == {"晶圆制造": False, "中芯": True, "华虹": True, "长鑫": True,
                     "配套材料": False, "光刻胶": True, "特气": True}
    for i in items:
        assert "（" not in i["name"] and "）" not in i["name"]


def test_trim_only_in_target_slot_inside_group() -> None:
    """尾部剥离只在**括号内标的位**（`：`/`=` 右侧）生效；括号外与其它位置只认整词。

    回归背景：
      · 2026-11-14 上午：放宽到全文时「数字人民币」被剥成「数字人」(920670.BJ) —— 假链接；
      · 2026-11-14 下午：括号外也是环节名，`数据要素`(399418.SZ) 这类指数别名同理不能进来。
    """
    stub = {"数字人": "920670.BJ", "张江高科": "600895.SH"}
    plain = cs._chain_detail("数字货币/数字人民币", stub.get)
    assert [(i["name"], i["kind"]) for i in plain] == [("数字货币", "label"), ("数字人民币", "label")]

    slot = cs._chain_detail("（标的：张江高科参股）", stub.get)
    assert [(i["name"], i["kind"]) for i in slot] == [("标的", "label"), ("张江高科参股", "stock")]

    outer = cs._chain_detail("标的：张江高科参股", stub.get)
    assert all(i["kind"] == "label" for i in outer), "括号外不参与名→码解析"


# 括号外 5 个误挂的码（2026-11-14 主人实测）：指数/行业词别名，链位绝不能再出现
ALIAS_CODES = {"新能源车": "399417.SZ", "数据要素": "399418.SZ", "机器人": "300024.SZ"}


@pytest.mark.parametrize("cell", [
    "新能源车/储能/eVTOL/机器人",                                  # 固态电池 down
    "新能源车/机器人/风电",                                        # 稀土永磁 down
    "AI 应用层：数据要素/智能体/行业软件（三维天地/慧辰股份/海量数据）",   # AI应用/数据要素 mid
])
def test_outer_tokens_never_resolve(cell: str) -> None:
    """括号外一律 label：即使名表能解析（新能源车/数据要素/机器人）也不给码。"""
    items = cs._chain_detail(cell, ALIAS_CODES.get)
    outer = [i for i in items if not i["in_group"]]
    assert outer, items
    assert all(i["kind"] == "label" and i["code"] == "" for i in outer), outer
    assert all(i["code"] not in set(ALIAS_CODES.values()) for i in items), items


def test_group_targets_still_resolve() -> None:
    """同一句里括号内仍按名表判定（不能因为外层的改动连坐）。"""
    stub = dict(ALIAS_CODES, 三维天地="301159.SZ", 慧辰股份="688500.SH")
    items = cs._chain_detail("AI 应用层：数据要素/智能体/行业软件（三维天地/慧辰股份）", stub.get)
    assert [(i["name"], i["kind"]) for i in items] == [
        ("AI 应用层", "label"), ("数据要素", "label"), ("智能体", "label"), ("行业软件", "label"),
        ("三维天地", "stock"), ("慧辰股份", "stock")]


def test_stock_names_outside_brackets_are_label() -> None:
    """结构规则：没有括号的格子里就算写的是真股票名，也是环节名（不可点）。"""
    items = cs._chain_detail("斯瑞新材/三角防务", {"斯瑞新材": "688102.SH", "三角防务": "300775.SZ"}.get)
    assert [(i["name"], i["kind"], i["code"]) for i in items] == [
        ("斯瑞新材", "label", ""), ("三角防务", "label", "")]


def test_fullwidth_semicolon_splits_outer_groups() -> None:
    """外层全角分号也必须断开：`…（A）；动捕/视觉/IMU（B）` 不得粘出「；动捕」。"""
    names = [i["name"] for i in cs._chain_detail(
        "仿真平台/世界模型（索辰科技）；动捕/视觉（利亚德/奥普特）", resolve)]
    assert names == ["仿真平台", "世界模型", "索辰科技", "动捕", "视觉", "利亚德", "奥普特"]


def test_halfwidth_brackets_supported() -> None:
    """半角括号同样是分组（MD 里偶尔混用）。"""
    items = cs._chain_detail("封测(通富微电/长电科技)", resolve)
    assert [(i["name"], i["in_group"]) for i in items] == [
        ("封测", False), ("通富微电", True), ("长电科技", True)]


def test_latin_slash_is_one_token() -> None:
    """`DRAM/NAND` 两侧都是英文字母 → 英文复合词不拆；中英之间的 `/` 照拆。"""
    names = [i["name"] for i in cs._chain_detail("DRAM/NAND 制造 + 利基设计", resolve)]
    assert names == ["DRAM/NAND 制造", "利基设计"]
    assert [i["name"] for i in cs._chain_detail("光模块/CPO", resolve)] == ["光模块", "CPO"]


def test_no_resolver_is_all_label() -> None:
    """未注入解析器（名表不可用）→ 全部 label，不猜、不崩。"""
    items = cs._chain_detail("封测（通富微电/长电科技）", None)
    assert [i["kind"] for i in items] == ["label", "label", "label"]
    assert [i["code"] for i in items] == ["", "", ""]


def test_resolver_exception_is_safe() -> None:
    """解析器抛错 → 该 token 变 label，不影响主流程。"""
    def boom(_n):
        raise RuntimeError("boom")

    assert [i["kind"] for i in cs._chain_detail("封测（通富微电）", boom)] == ["label", "label"]


# ---------------------------------------------------------------- 接进 load_index


MASTER_CHAIN = """# 概念与催化总表

> 最后更新：2026-11-14

## 产业链链位映射（异动归因用）

| 概念 | 上游 | 本环节 | 下游 |
|------|------|--------|------|
| 国产DUV光刻机量产 | 光学系统/光源/温控部件（茂莱光学/海立股份） | 整机（上海微电子/御量升，未上市；A股映射=张江高科参股） | 晶圆制造（中芯/华虹/长鑫）+ 配套材料（光刻胶/特气） |
| 汽车·华为智行链 | 汽车电子/座舱/热管理（德赛西威/均胜电子） | 整车（华为智选/鸿蒙智行：赛力斯/江淮汽车） | 零部件/后市场（冠盛股份/凯迪股份/襄阳轴承） |
"""


@pytest.fixture()
def chain_dir(tmp_path: Path) -> Path:
    cs.reset_cache()
    (tmp_path / cs.MASTER_NAME).write_text(MASTER_CHAIN, encoding="utf-8")
    yield tmp_path
    cs.reset_cache()


def test_load_index_wires_detail_and_keeps_raw(chain_dir: Path) -> None:
    """接线：load_index 传进来的 name_to_code 必须落到三格 detail；原文一字不改。"""
    d = cs.load_index(chain_dir, name_to_code=resolve)
    assert d["ok"] is True
    by = {c["name"]: c for c in d["chain"]}

    duv = by["国产DUV光刻机量产"]
    assert duv["up"] == "光学系统/光源/温控部件（茂莱光学/海立股份）"
    assert duv["mid"] == "整机（上海微电子/御量升，未上市；A股映射=张江高科参股）"
    assert duv["down"] == "晶圆制造（中芯/华虹/长鑫）+ 配套材料（光刻胶/特气）"
    assert [i["kind"] for i in duv["up_detail"]] == ["label", "label", "label", "stock", "stock"]
    assert [i["name"] for i in duv["mid_detail"]][-1] == "张江高科参股"
    assert len(duv["down_detail"]) == 7

    hw = by["汽车·华为智行链"]
    assert [i["kind"] for i in hw["mid_detail"]] == ["label", "label", "label", "stock", "stock"]
    assert [i["code"] for i in hw["mid_detail"] if i["kind"] == "stock"] == \
        ["601127.SH", "600418.SH"]


def test_load_index_without_resolver_all_label(chain_dir: Path) -> None:
    d = cs.load_index(chain_dir)
    for c in d["chain"]:
        for key in ("up_detail", "mid_detail", "down_detail"):
            assert c[key], "detail 必须有内容：%s %s" % (c["name"], key)
            assert all(i["kind"] == "label" and i["code"] == "" for i in c[key])


def test_top_level_contract_unchanged(chain_dir: Path) -> None:
    """契约只加不改：顶层 key 集合原样，链位卡仍是 name/up/mid/down + 新增三个 detail。"""
    d = cs.load_index(chain_dir, name_to_code=resolve)
    assert set(d) == {"ok", "updated", "active", "watch", "chain", "archived",
                      "counts", "sector_map", "unresolved_targets", "sector_unresolved"}
    assert set(d["chain"][0]) == {"name", "up", "mid", "down",
                                  "up_detail", "mid_detail", "down_detail"}


# ---------------------------------------------------------------- 前端消费（静态检查）


def test_frontend_consumes_kind_and_in_group() -> None:
    """frontend.js 必须用上 kind / in_group（否则服务端白拆）。"""
    src = (PLUGIN_DIR / "frontend.js").read_text(encoding="utf-8")
    assert "it.in_group === true" in src, "detailItems 需透出 in_group"
    assert "it.kind ||" in src, "detailItems 需透出 kind（缺省按 code 有无推断）"
    assert "chainChipsHtml(" in src, "链位段需用分组渲染"
    assert "cp-chip-grp" in src, "括号内标的需有视觉分组容器"
    assert "chipHtml(o.name, o.code, o.kind)" in src, "chip 需按 kind 区分类别/标的"
    assert "cp-chip-off" in src, "无码仍必须显式不可点"
