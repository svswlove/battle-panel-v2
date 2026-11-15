# -*- coding: utf-8 -*-
"""日内演化 · 模式隔离与指数叠加（N20261027-100741-29 / -30 / -31 / -32）。

四条标注的共同点：**左侧 pane 把 AMV 的东西无条件显示在行情模式里**，加上轨迹快照没有
`idx_chg` 导致「行情涨跌」永远空白。本文件守两件事：

  ① 模式隔离（纯函数）：quote 模式不出现预测浮层 / 全A 信息行 / AMV 读数，标题为「行情涨跌」；
     amv 模式才出现上述三项。预测快照非今日 → 明示 `历史预测 YYYY-MM-DD HH:MM`（日期可注入）。
  ② 六指数叠加真的可用：勾选 → path 增加，逐个 on/off 实际改变 path；一个都不勾也不空白
     （回退前两个并说明）；缺 idx_chg 时给明确「无数据」说明，不补 0。

不跑旧页源码（那是 tests/evo_align.mjs 的事）；本文件只跑真实 `frontend/evo.js`。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

PANEL = Path(__file__).resolve().parents[4]      # projects/panel-v2
PLUGIN = PANEL / "app" / "plugins" / "intraday"
EVO = PLUGIN / "frontend" / "evo.js"
RUNNER = PLUGIN / "tests" / "evo_mode.mjs"


def _run() -> dict:
    proc = subprocess.run(["node", str(RUNNER)], capture_output=True, text=True,
                          timeout=120, cwd=str(PANEL))
    lines = [x for x in proc.stdout.strip().splitlines() if x.strip().startswith("{")]
    if not lines:
        pytest.fail("node 无 JSON 输出：\n%s\n%s" % (proc.stdout[-2000:], proc.stderr[-2000:]))
    return json.loads(lines[-1])["cases"]


@pytest.fixture(scope="module")
def out() -> dict:
    return _run()


# ---------------------------------------------------------------- ① 模式隔离

def test_quote_mode_hides_forecast_and_amv_info(out: dict) -> None:
    """标注 -29：quote 模式仍显示 AMV 预测浮层 / 全A 信息行 → 必须都没有。"""
    c = out["modeIsolation"]
    assert c["quoteHasInfo"] == 0, "行情涨跌模式还挂着全A 信息行（AMV 专属数值）"
    assert c["quoteHasAmvSub"] is False, "行情涨跌模式标题里还有 AMV 读数"
    assert c["quoteSvg"] == 1 and c["amvSvg"] == 0, "quote 模式该出 quote 图"


def test_quote_mode_title_is_market(out: dict) -> None:
    """标注 -32：标题要改成行情，不能还叫「活跃指数」。"""
    c = out["modeIsolation"]
    assert c["quoteTitle"].startswith("行情涨跌"), "标题未改：%r" % c["quoteTitle"]


def test_amv_mode_keeps_forecast_and_info(out: dict) -> None:
    """AMV 模式保留 AMV 信息行（预测浮层已于 2026-11-05 按标注 73 整块删除）。"""
    c = out["modeIsolation"]
    assert c["amvTitle"].startswith("活跃指数"), "AMV 模式标题应保留活跃指数：%r" % c["amvTitle"]


# ---------------------------------------------------------------- ② 六指数叠加

def test_idx_toggle_changes_path(out: dict) -> None:
    """标注 -30：勾一个 → 一条线；再勾一个 → 多一条；path 真的变了。"""
    c = out["idxToggle"]
    assert c["buttons"] == ["000001.SH", "399006.SZ", "000688.SH", "000300.SH", "000852.SH", "399303.SZ"]
    assert c["pathOne"] == 1, "勾一个指数应只有 1 条 path（实际 %d）" % c["pathOne"]
    assert c["pathTwo"] == 2, "勾两个应 2 条 path（实际 %d）" % c["pathTwo"]
    assert c["changed"] is True, "逐个开关没改变 path（点了没反应）"
    assert c["labelOne"] == 1 and c["labelTwo"] == 2, "末值标签应与曲线数一致"


def test_quote_mode_has_all_baseline(out: dict) -> None:
    """N20261028-140428-39：行情涨跌图要有全A 基准线（与 AMV 模式的主线同一条）。

    基准线不随指数勾选变化：勾一个在、一个不勾也在（否则「基准」会跟着选择跑）。
    """
    c = out["idxToggle"]
    assert c["allbaseOne"] == 1, "勾一个指数时缺全A 基准线"
    assert c["allbaseNone"] == 1, "一个指数都不勾时缺全A 基准线"


def test_quote_mode_never_blank(out: dict) -> None:
    """一个指数都不勾时不留白图：回退前两个并给出说明。"""
    c = out["idxToggle"]
    assert c["pathNone"] > 0, "未勾选时图是空的（标注 -30 的「暂无数据」就是这么来的）"
    assert c["noneNotBlank"] is True


def test_amv_mode_still_overlays_idx(out: dict) -> None:
    """AMV 模式的六指数叠加是旧行为，不许被这次改动弄丢。"""
    c = out["idxToggle"]
    assert c["amvNoIdx"] == 1, "AMV 模式没勾指数时应只有活跃指数一条"
    assert c["amvWithIdx"] == 2, "AMV 模式勾一个指数应变成两条"


def test_missing_idx_is_explained_not_zeroed(out: dict) -> None:
    """缺 idx_chg：给明确说明，不补 0（0 会被读成平盘）。"""
    c = out["missing"]
    assert "缺 idx_chg" in c["missingMsg"], "缺字段的说明不明确：%r" % c["missingMsg"]
    assert c["missPathZero"] == 0, "缺数据时不该画线（更不该补 0）"
    assert "无值" in c["nullMsg"], "有字段但全空要说「无值」：%r" % c["nullMsg"]


def test_history_day_has_its_own_message(out: dict) -> None:
    """历史交易日：后端**不给历史补**（不拿今天曲线贴历史），前端话要说得准。"""
    c = out["missing"]
    assert "历史交易日" in c["histMsg"], "历史日的话术不对：%r" % c["histMsg"]
    assert "当日指数分钟线不可用" not in c["histMsg"], "历史日不该说成「今日不可用」"


def test_partial_idx_draws_only_present(out: dict) -> None:
    """部分指数有值 → 只画有的那条；全空的指数如实说无值。"""
    c = out["missing"]
    assert c["partialSvg"] == 1, "只有上证有值时应只画 1 条"
    assert "无值" in c["partialMsg"] or "缺" in c["partialMsg"]


# ---------------------------------------------------------------- ③ 静态：隔离点写死在源码里

def test_mode_isolation_is_in_source() -> None:
    src = EVO.read_text(encoding="utf-8")
    assert "amv-fc-ov" not in src, "预测浮层已于 2026-11-05（标注 73）删除，不该再出现"
    assert "isAmv ? '\\n        ' + allInfoHtml(st) : ''" in src, "allInfoHtml 没有按模式隔离"
    assert "MODE_TITLE" in src
