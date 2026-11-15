# -*- coding: utf-8 -*-
"""AMV 预测实时源（2026-10-27）· 前端刷新闸门与频次。

守「盘中卡按需刷新 amv_live」这件事的可见行为（真实 `frontend/amvrefresh.js` + 假 api）：
  ① 频次：5 个 20s tick 只发 2 次 POST（60s 节流），不是每拍盲 POST；
  ② 非交易时段不调用：周末 / 午休 / 收盘后 / 竞价前 → 零 POST；
  ③ 成功后重新 GET amv_live.json，回填的是当天那份（预测浮层才能判「今日」而非历史）；
  ④ 失败（ok:false / 抛错 / GET 失败）→ 明示「刷新失败」，不把旧值写成实时；
  ⑤ 卸载 / 整卡 loading / 页面不可见 → 不刷（防递归）。

真实生产验收（真 POST + 真写盘）由 01 在生产进程里做，本文件全部走假 api。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

PANEL = Path(__file__).resolve().parents[4]      # projects/panel-v2
PLUGIN = PANEL / "app" / "plugins" / "intraday"
MODULE = PLUGIN / "frontend" / "amvrefresh.js"
RUNNER = PLUGIN / "tests" / "amv_refresh.mjs"


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


# ---------------------------------------------------------------- ② 频次

def test_post_at_most_once_per_60s(out: dict) -> None:
    c = out["throttle"]
    assert c["postPath"] == "/api/amv_refresh", "POST 必须打已有的 /api/amv_refresh：%r" % c["postPath"]
    assert c["getPath"] == "/api/amv_live.json", "刷新后必须重取 amv_live.json"
    assert c["posts"] == 2, "5 个 20s tick（共 80s）应只发 2 次 POST，实测 %d" % c["posts"]
    assert c["actedFlags"] == [True, False, False, True, False], c["actedFlags"]
    assert c["whys"][1] == "throttled", "第二拍应因 60s 节流被跳过：%r" % c["whys"]
    assert c["gets"] == c["posts"], "每成功刷新只重取一次"


# ---------------------------------------------------------------- ③ 非交易时段

def test_no_post_outside_continuous_auction(out: dict) -> None:
    s = out["sessions"]
    for name in ("weekend", "sunday", "lunch", "afterClose", "preAuction", "auctionEdge1130"):
        assert s[name]["posts"] == 0, "%s 不该 POST，实测 %d" % (name, s[name]["posts"])
        assert s[name]["whys"] == ["off-session"], "%s 跳过原因：%r" % (name, s[name]["whys"])
    assert s["afternoonOpen"]["posts"] == 1, "13:00 开盘必须恢复刷新"


def test_auction_boundaries(out: dict) -> None:
    p = out["plan"]
    assert p["at0930"] is True and p["at0929"] is False
    assert p["at1129"] is True and p["at1130"] is False, "11:30 整点为午休起点"
    assert p["at1300"] is True and p["at1500"] is False
    assert p["sat"] is False
    assert p["gap"] == 60000


# ---------------------------------------------------------------- ④ 成功 / 失败

def test_success_refills_today_snapshot(out: dict) -> None:
    c = out["success"]
    assert c["docs"] == 1, "成功后必须重取一次 amv_live.json"
    assert c["day"] == "2026-10-27", "回填必须是当天那份（预测才能判今日）：%r" % c["day"]
    assert c["notes0"] == "", "成功不该留失败文案：%r" % c["notes0"]
    assert c["staleWhy"] == "stale-file", "首轮文件非今日应触发刷新：%r" % c["staleWhy"]
    assert c["stalePosts"] == 1
    assert c["staleDocDay"] == "2026-10-27", "刷新后文件日期必须变成当天"


def test_failure_is_explicit(out: dict) -> None:
    c = out["failure"]
    assert c["staleNoteStartsFail"] is True, "失败文案必须以「刷新失败」开头：%r" % c["okFalseNote"]
    assert "超时" in c["okFalseNote"]
    assert c["thrownNote"].startswith("刷新失败 "), c["thrownNote"]
    assert c["getFailNote"].startswith("刷新失败 "), c["getFailNote"]
    assert c["okFalseDocs"] == 0, "失败不得替换当前 AMV"
    assert c["okFalseSecondWhy"] == "throttled", "失败也算一次，防每 20s 重试打爆"


# ---------------------------------------------------------------- ⑤ 防递归 / 生命周期

def test_no_post_when_unmounted_loading_or_hidden(out: dict) -> None:
    g = out["guards"]
    assert g["unmountedPosts"] == 0
    assert g["loadingPosts"] == 0, "S.loading 期间不得 POST（防递归）"
    assert g["hiddenPosts"] == 0, "页面不可见不刷（与 core 的可见性契约一致）"
    assert g["whys"] == ["unmounted", "loading", "hidden"], g["whys"]


# ---------------------------------------------------------------- ⑥ 静态约束

def test_module_is_io_free_and_esm() -> None:
    """amvrefresh.js 只做判定与编排：零 DOM、零裸 fetch、零写盘、零外部 import。"""
    src = MODULE.read_text(encoding="utf-8")
    code = "\n".join(x for x in src.splitlines() if not x.strip().startswith(("*", "//", "/*")))
    for bad in ("fetch(", "XMLHttpRequest", "localStorage", "document.", "window.", "import "):
        assert bad not in code, "amvrefresh.js 不得出现 %r（IO/依赖一律由调用方注入）" % bad


def test_frontend_wires_refresher_once() -> None:
    """编排接入：建实例 1 处、timer 1 处、unmount 清理 1 处，且不挂进 core refresh()。"""
    fe = (PLUGIN / "frontend.js").read_text(encoding="utf-8")
    assert fe.count("amvrefresh.createRefresher(") == 1
    assert "amvrefresh.planRefresh(" not in fe, "判定逻辑不该在 frontend.js 里二次实现"
    assert fe.count("setInterval(() => { amvRefresh.tick(); }") == 1
    assert "clearInterval(S.amvTimer)" in fe
    assert "/api/amv_refresh" in fe or "/api/amv_refresh" in MODULE.read_text(encoding="utf-8")
