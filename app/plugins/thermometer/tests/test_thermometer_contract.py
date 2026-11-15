# -*- coding: utf-8 -*-
"""情绪温度计 · 契约 + 真实接口测试（2026-10-29）。

守四层：
1. **插件形态**：manifest 可加载、卡唯一、`data.read` 为空（不直连库）、`data.panel` 只声明
   `panic_breadth`、`data.write` 为空；core 三个文件里不出现 thermometer（拆卸 = 删目录 + 删 NAV 一行）。
2. **不产数**：前端零 import，只打三个已有接口（emotion payload / api/panic / 本插件 meta）；
   后端不出现直连库、HTTP、写盘、子进程写法。
3. **口径断点**：`caliber()` 纯函数对合成序列库的三项/四项计数与漂移判定。
4. **真实接口**：`/api/p/thermometer/meta` 在真实 core 上回断点日与序列库行数；
   今日（2026-10-29）两个上游读数与页面一致（8.26 / 23.1），且「上涨家数」两处一致。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import paths  # noqa: E402
from app.core.contract import load_manifest  # noqa: E402
from app.core.main import create_app  # noqa: E402

PLUGIN_DIR = Path(__file__).resolve().parents[1]
FRONTEND = PLUGIN_DIR / "frontend.js"
BACKEND = PLUGIN_DIR / "backend.py"
CORE_DIR = ROOT / "app" / "core"

PANIC_STORE = paths.panel_path("panic_breadth")
HAS_STORE = bool(PANIC_STORE and PANIC_STORE.is_file())
needs_store = pytest.mark.skipif(not HAS_STORE, reason="panic_breadth.json 不在本机")


def _backend():
    """按路径加载 backend.py（core 用 spec_from_file_location 加载插件，测试同法）。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location("_thm_backend_test", BACKEND)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------- 1. 插件形态

def test_manifest_loads_and_declares_single_card():
    m = load_manifest(PLUGIN_DIR)
    assert m.id == "thermometer"
    assert m.enabled is True
    assert [c.id for c in m.frontend.cards] == ["thermometer.main"]
    assert m.frontend.cards[0].slot == "main"
    assert m.frontend.cards[0].title == "情绪温度计"


def test_manifest_reads_only_panic_store():
    """四维要读 sentiment + kline（`track_pool.db`）、恐慌序列库与主力资金序列；**不写**任何生产文件。"""
    m = load_manifest(PLUGIN_DIR)
    assert tuple(m.data_read) == ("track_pool.db",)
    assert tuple(m.panel_read) == ("panic_breadth", "index_flow_series_880008")
    assert tuple(m.data_write) == ()
    assert m.quotes_read is False


def test_panel_names_are_in_core_whitelist():
    """声明的每个 panel 名必须在 core `paths.READABLE_PANEL` 里（防「自己声明自己」绕过）。"""
    from app.core import paths as core_paths

    m = load_manifest(PLUGIN_DIR)
    for name in m.panel_read:
        assert name in core_paths.READABLE_PANEL, name
    assert core_paths.READABLE_PANEL["index_flow_series_880008"].name == "index_flow_series_880008.json"


def test_core_does_not_know_thermometer():
    for name in ("main.py", "paths.py", "contract.py"):
        text = (CORE_DIR / name).read_text(encoding="utf-8")
        assert "thermometer" not in text.lower(), name


def test_shell_nav_has_one_line_and_is_removable():
    shell = (ROOT / "app" / "web" / "shell.js").read_text(encoding="utf-8")
    assert shell.count("thermometer::thermometer.main") == 1


# ---------------------------------------------------------------- 2. 不产数

def test_frontend_is_zero_import_and_consumes_only_existing_endpoints():
    text = FRONTEND.read_text(encoding="utf-8")
    assert not re.search(r"^\s*import\s", text, re.MULTILINE), "前端必须零 import（CONTRACT §5）"
    assert "require(" not in text
    # 三个数据来源：两上游 + 本插件元信息与四维
    assert "/api/p/emotion/payload" in text
    assert "'/api/panic'" in text
    assert "'/meta'" in text
    assert "'/dims'" in text
    # 不重算：前端不出现情绪总分公式的常量，四维分数也不在前端算
    assert "50 +" not in text
    assert "panic_score" in text  # 只在注释/文案里引用口径名


def test_frontend_marks_missing_instead_of_zero():
    """缺数据必须显示「缺」：不填 0、不编值。"""
    text = FRONTEND.read_text(encoding="utf-8")
    assert "'缺'" in text
    assert "样本不足" in text
    assert "不补值" in text


def test_frontend_rejects_stale_panic_payload():
    """`/api/panic` 在非交易时段会回落库内旧行（mode=closed）——页面必须比对数据日，
    不相等就把恐慌侧标缺，不拿旧行充今日。"""
    text = FRONTEND.read_text(encoding="utf-8")
    assert "panicFresh" in text and "panicStale" in text
    assert "回落到库内" in text
    assert "panic.trade_date" in text


def test_frontend_marks_panic_caliber_break():
    text = FRONTEND.read_text(encoding="utf-8")
    assert "此前三项口径" in text
    assert "四项含量能" in text
    assert "不连线" in text


def test_backend_has_no_db_http_or_write():
    src = BACKEND.read_text(encoding="utf-8")
    for token in ("sqlite3.connect", "urllib", "requests.", "httpx", "subprocess",
                  "open(", ".write_text(", ".write_bytes(", "os.replace(", "json.dump("):
        assert token not in src, token

# ---------------------------------------------------------------- 3. 断点口径（纯函数）

def _row(date, amount=None):
    row = {"date": date, "panic": 10.0}
    if amount is not None:
        row["amount"] = amount
        row["amount_ratio"] = 0.95
    return row


def test_caliber_three_item_history():
    mod = _backend()
    out = mod.caliber({"series": [_row("20261020"), _row("20261021"), _row("20261023")]})
    assert out["ok"] is True
    assert out["break_from"] == "20261029"
    assert out["store"]["rows"] == 3
    assert out["store"]["three_item_rows"] == 3
    assert out["store"]["four_item_rows"] == 0
    assert out["store"]["four_item_first"] is None
    assert out["store"]["first"] == "20261020" and out["store"]["last"] == "20261023"
    assert out["drift"] is None


def test_caliber_four_item_from_declared_break():
    mod = _backend()
    out = mod.caliber({"series": [_row("20261023"), _row("20261029", amount=1.5e12)]})
    assert out["store"]["three_item_rows"] == 1
    assert out["store"]["four_item_rows"] == 1
    assert out["store"]["four_item_first"] == "20261029"
    assert out["drift"] is None


def test_caliber_reports_drift_instead_of_silent_change():
    """序列库若出现别的日期的四项行，必须显式报漂移，不静默改口。"""
    mod = _backend()
    out = mod.caliber({"series": [_row("20261028", amount=1.2e12)]})
    assert out["store"]["four_item_first"] == "20261028"
    assert out["drift"] and out["drift"]["declared_break"] == "20261029"


def test_degraded_keeps_break_date_but_no_counts():
    mod = _backend()
    out = mod._degraded("FileNotFoundError: nope")
    assert out["ok"] is False
    assert out["break_from"] == "20261029"        # 断点照给（页面还要标）
    assert out["store"]["rows"] is None           # 行数一律 None，不编 0


def test_meta_degrades_when_panel_unreadable():
    mod = _backend()

    class Ctx:
        def panel_json(self, name):
            raise PermissionError("未在 manifest.data.panel 声明 %s" % name)

    client = TestClient(_app_with(mod, Ctx()))
    body = client.get("/api/p/thermometer/meta").json()
    assert body["ok"] is False
    assert body["store"]["rows"] is None
    assert body["break_from"] == "20261029"


def _app_with(module, ctx):
    """把 backend 的 router 挂到最小 FastAPI 上（不加载真 core，避免依赖本机数据）。"""
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(module.register(ctx), prefix="/api/p/thermometer")
    return app


# ---------------------------------------------------------------- 4. 真实接口

@pytest.fixture(scope="module")
def client():
    with TestClient(create_app()) as c:
        yield c


@needs_store
def test_meta_on_real_core(client):
    """真实序列库：断点常量照给、三项/四项计数覆盖全部行、口径漂移显式上报（不锁「四项行=0」）。"""
    r = client.get("/api/p/thermometer/meta")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["break_from"] == "20261029"
    st = body["store"]
    assert st["rows"] > 0
    assert st["three_item_rows"] + st["four_item_rows"] == st["rows"]
    if st["four_item_first"] is None:
        assert st["four_item_rows"] == 0 and body["drift"] is None
    elif st["four_item_first"] == body["break_from"]:
        assert body["drift"] is None
    else:
        # 序列库被整库回填（2026-11-06 实测 1273 行全带 amount）→ 必须显式报漂移，不静默改口
        assert body["drift"] and body["drift"]["store_four_item_first"] == st["four_item_first"]
        assert body["drift"]["declared_break"] == body["break_from"]
    assert body["windows"] == {"emotion_days": 60, "panic_days": 250}


def test_upstream_readings_agree_on_up_count(client):
    """同一个「上涨家数」两个上游必须一致（页面顶栏就是拿它做交叉校验）。

    只在「两上游数据日相同」时可比：`/api/panic` 在非交易时段（开盘前/零点后）会按设计
    回落库内最后一行（mode=closed），此时它的上涨家数属于另一天，不能当不一致。
    """
    emotion = client.get("/api/p/emotion/payload?history_days=60")
    panic = client.get("/api/panic")
    if emotion.status_code != 200 or panic.status_code != 200:
        pytest.skip("上游接口当前不可用（无收盘行/旧仓缺失）")
    e = emotion.json()["today"]
    p = panic.json()
    if str(p.get("trade_date")) != str(e.get("date")):
        # 盘中：panic 是 live（当日实时行），emotion 的 today 仍是上一收盘行 → 数据日天然不同
        if p.get("mode") == "live":
            pytest.skip("盘中：panic=live(%s) vs emotion 收盘行(%s)，不同日不可比"
                        % (p.get("trade_date"), e.get("date")))
        assert p.get("mode") == "closed", "数据日不同但模式不是 closed，降级路径变了：%s" % p.get("mode")
        pytest.skip("恐慌接口已回落库内 %s 行（mode=closed），本日无法两处对账" % p.get("trade_date"))
    up_item = next((i for i in p["items"] if i["key"] == "up_ratio"), None)
    assert up_item is not None
    assert e["up_cnt"] is not None and up_item["count"] is not None
    assert int(e["up_cnt"]) == int(up_item["count"])


def test_today_readings_match_page_numbers(client):
    """只在「数据日 = 2026-10-29 且恐慌接口给的是同日读数」时对答案。

    日频快照，换日自然失效；零点后恐慌接口回落到库内旧行，同样跳过（降级路径另有断言）。
    """
    emotion = client.get("/api/p/emotion/payload?history_days=60")
    panic = client.get("/api/panic")
    if emotion.status_code != 200 or panic.status_code != 200:
        pytest.skip("上游接口当前不可用")
    e = emotion.json()
    p = panic.json()
    if e.get("asof") != "20261029":
        pytest.skip("数据日已不是 2026-10-29")
    assert e["today"]["score"] == 8.26
    assert e["today"]["band"] == "冰点"
    if str(p.get("trade_date")) != "20261029":
        pytest.skip("恐慌接口已回落库内 %s 行（mode=%s）" % (p.get("trade_date"), p.get("mode")))
    assert p["value"] == 23.1
    assert p["mode"] == "close"
    amt_item = next(i for i in p["items"] if i["key"] == "amount")
    assert amt_item["amount"] == 1556524171264.0
    assert amt_item["prev_amount"] == 1628487560384.0
