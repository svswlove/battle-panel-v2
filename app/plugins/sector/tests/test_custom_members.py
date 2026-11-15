"""自建板块（999 段）成分股全量守门（2026-10-25 夏天口径）。

口径：自建板块成分股列表必须**全量显示，不做任何百分比截断**——10% 比例规则在
个位数样本（最小板块 3 只）上退化为只显示 1 只，等于把板块内容全隐掉。
板块榜（横向比较，前 10%/后 10%）的 _top_k 比例规则**不在**本口径内，仍成立。

边界（夏天）：不动 `stock_top` 表与 TOP_N（被 detail 默认路径 / _top_of_sector /
api_leaders 三处读）；`RS_MEMBER_CAP=100`（2026-10-26 主人定）对 999 段不构成风险；超过 100 的板块按当前排序只下发前 100，`n_members` 仍为真值。

真实库模式：999 段今天由 `夏天-activity/工具/concept_to_custom.py` 同步（26 个概念，
999001~999026）。数据不存在则 skip（换机/重置后本测试自动退场）。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

PANEL = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PANEL / "app"))

from app.plugins.sector import backend  # noqa: E402

AMV_DB = PANEL.parents[1] / "data" / "amv_board.db"
POOL_DB = PANEL.parents[1] / "data" / "ths_board.db"


def _count_999() -> int:
    try:
        conn = sqlite3.connect(str(AMV_DB))
        try:
            return conn.execute(
                "SELECT COUNT(DISTINCT code) FROM sector_meta WHERE code LIKE '999%'").fetchone()[0]
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - 库不可用 → 无 999 段
        return 0


pytestmark = pytest.mark.skipif(_count_999() == 0, reason="生产库无 999 段自建板块（未同步）")


class _RealCtx:
    """只读 ctx：db_read 直连生产库（与 core 门面同语义：只读连接、用完即关）。"""

    def db_read(self, name: str):
        p = {"amv_board.db": AMV_DB, "ths_board.db": POOL_DB}.get(name)
        if p is None:
            raise PermissionError("%s 不在白名单" % name)
        conn = sqlite3.connect(str(p))
        conn.row_factory = sqlite3.Row          # backend._fetch 按字典式取列
        return conn
        # 连接由 backend._db_conn 用完关闭

    def json_read(self, name: str):
        raise PermissionError("%s 不在白名单" % name)

    _LIBS = {"amv_regime": "app.lib.amv_regime", "amv_contrib": "app.lib.amv_contrib",
             "amv_struct_tag": "app.lib.amv_struct_tag"}

    def lib(self, name: str):
        # 与 core 门面同款保护（contract.py lib()）：口径库模块体自己会
        # `sys.path.insert(0, 目录)` 且不清理（amv_contrib.py:37 实测）；裸 import 会把
        # app/lib 永久留在 sys.path，撞穿契约门禁
        # test_plugin_cannot_import_regime_directly_after_lib_load。这里用快照恢复。
        import importlib.util  # noqa: PLC0415
        mod_path = self._LIBS.get(name)
        if mod_path is None:
            raise PermissionError("lib 不在白名单：%s" % name)
        p = PANEL / "app" / "lib" / (name + ".py")
        module_name = "_test_lib_%s" % name
        module = sys.modules.get(module_name)
        if module is not None:
            return module
        spec = importlib.util.spec_from_file_location(module_name, p)
        module = importlib.util.module_from_spec(spec)
        path_before = list(sys.path)
        parent = str(p.parent)
        if parent not in sys.path:
            sys.path.append(parent)
        try:
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        finally:
            sys.path[:] = [x for x in path_before if x != parent]
        return module


def _first_999() -> tuple:
    """取一个 999 段板块的 (带后缀码, 成分股数)。取成员数最小的（口径最脆弱处：个位数样本）。"""
    conn = sqlite3.connect(str(POOL_DB))
    try:
        row = conn.execute(
            "SELECT concept_code, COUNT(*) AS n FROM concept_member "
            "WHERE concept_code LIKE '999%' GROUP BY concept_code ORDER BY n ASC LIMIT 1").fetchone()
    finally:
        conn.close()
    return (row[0], row[1]) if row else ("", 0)


def test_custom_members_full_no_truncation() -> None:
    """999 段成分股全量：members?full=1 的 n == n_total == concept_member 表行数。

    这是夏天口径的核心断言：不按百分比截断（n < n_total 即破门）。
    用成员数最小的板块（个位数样本）当哨兵。
    """
    code_ti, n_tbl = _first_999()
    assert code_ti, "999 段存在但 concept_member 无成分行"
    out = backend.build_members(_RealCtx(), code_ti, None, limit=0, full=True, period=1)
    assert out["n"] == out["n_total"] == n_tbl, (
        "999 段成分股被截断：n=%s n_total=%s 表内=%s（口径：全量不截）"
        % (out["n"], out["n_total"], n_tbl))
    assert out["source"] == "full"


def test_custom_members_bare_code_fallback() -> None:
    """裸码（不带 .TI）也必须拿得到成分股（2026-10-25 01 修复的 level 反查落空 → 404）。

    带后缀是主路径；裸码是兼容加固（手输 URL / 外部脚本）。响应 code 保持调用方原样。
    """
    code_ti, n_tbl = _first_999()
    bare = code_ti.split(".")[0]
    out = backend.build_members(_RealCtx(), bare, None, limit=0, full=True, period=1)
    assert out["n"] == n_tbl, "裸码 %s 成分股数 %s != 表内 %s" % (bare, out["n"], n_tbl)
    assert out["n"] == out["n_total"]


def test_amv_research_concept_custom_full() -> None:
    """研究卡 CONCEPT 层对 999 段也全量（n_members == len(members)，无比例截断）。

    board scope 逐个生成 pools[sel]，唯一上限 RS_MEMBER_CAP=100 为绝对值（2026-10-26 主人定：按排序取前 100），
    对 999 段（≤28 只）恒不触发——本用例锁「999 段 n_members == members 数」。
    """
    code_ti, _ = _first_999()
    out = backend_compat_research("CONCEPT", code_ti)
    assert out["n_members"] == len(out["members"]), (
        "研究卡 CONCEPT 层 999 段被截断：n_members=%s members=%s"
        % (out["n_members"], len(out["members"])))


def backend_compat_research(level: str, code: str):
    """走 compat 插件的 build_amv_research（研究卡的真实数据源）。"""
    from app.plugins.compat import backend as compat_backend  # noqa: PLC0415
    return compat_backend.build_amv_research(_RealCtx(), level=level, code=code,
                                             days=12, live=0, scope="board")
