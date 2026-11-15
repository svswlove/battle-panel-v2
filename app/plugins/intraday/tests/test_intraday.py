# -*- coding: utf-8 -*-
"""intraday 插件契约测试：manifest / 摘要端点 / 静态禁令（全 mock，不碰真文件）。

守的是 docs/task_R1_intraday.md §3 与 docs/intraday_contract.md：
  · manifest：kind=domain、mount=plugin（不是 root，避免和 compat 抢根路径）、
    data.panel 只声明 amv_live_sector_track、data.write 为空
  · /summary：当日全A序列 + 领先/落后榜 + rel 口径（amv_pct − 同快照 all）
  · 缺文件 404 / 坏文件 503 / 无当日快照 ok:false（不编造零值）
  · 静态：无直连库、无行情源、无写盘、无 HTTP
"""
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import paths as core_paths  # noqa: E402
from app.core.contract import validate_manifest  # noqa: E402
from app.plugins.intraday import backend  # noqa: E402

PLUGIN_DIR = Path(__file__).resolve().parents[1]
BACKEND_PY = PLUGIN_DIR / "backend.py"
FRONTEND_JS = PLUGIN_DIR / "frontend.js"
MANIFEST = PLUGIN_DIR / "plugin.json"


def _manifest() -> Dict[str, Any]:
    with open(MANIFEST, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- FakeCtx


class FakeManifest:
    def __init__(self, panel_read=("amv_live_sector_track",)) -> None:
        self.panel_read = panel_read


class FakeQuotes:
    """`ctx.quotes.daily_bars` 的替身：只记调用、回放给定 bar 列表。

    `calls` 是本文件证明「有产物时没打 eltdx」的唯一凭据（不能只看返回值猜）。
    """

    def __init__(self, bars: Optional[List[Any]] = None, error: Optional[Exception] = None) -> None:
        self._bars = bars or []
        self._error = error
        self.calls: List[Dict[str, Any]] = []

    def daily_bars(self, code: str, n: int) -> List[Any]:
        self.calls.append({"code": code, "n": n})
        if self._error is not None:
            raise self._error
        return list(self._bars)


class _FakeClock:
    """交易日时钟替身：只实现本插件用到的 postclose()。"""

    def __init__(self, postclose: bool = True) -> None:
        self.value = postclose

    def postclose(self, now: Any = None) -> bool:
        return bool(self.value)


class FakeCtx:
    """只实现本插件用到的那几条读通道（panel_json / output_read / trade_clock / quotes），其余一律 AttributeError。"""

    def __init__(self, panels: Optional[Dict[str, Any]] = None,
                 error: Optional[Exception] = None,
                 panel_read=("amv_live_sector_track",),
                 outputs: Optional[Dict[Tuple[str, str], Any]] = None,
                 quotes: Optional[FakeQuotes] = None,
                 postclose: bool = True) -> None:
        self.manifest = FakeManifest(panel_read)
        self._panels = panels or {}
        self._error = error
        self.calls: List[str] = []
        self._outputs = outputs or {}
        self.output_calls: List[Tuple[str, str]] = []
        self.quotes = quotes if quotes is not None else FakeQuotes()
        # 2026-11-07：盘后产物只在盘后可用（盘中会滞后一天），默认按盘后测。
        self.trade_clock = _FakeClock(postclose)

    def panel_json(self, name: str) -> Any:
        self.calls.append(name)
        if self._error is not None:
            raise self._error
        if name not in self._panels:
            raise FileNotFoundError(name)
        return self._panels[name]

    def output_read(self, plugin_id: str, name: str, default: Any = None) -> Any:
        """跨插件只读（core contract.output_read 同口：缺失返回 default）。"""
        self.output_calls.append((plugin_id, name))
        return self._outputs.get((plugin_id, name), default)


def _client(ctx: FakeCtx) -> TestClient:
    app = FastAPI()
    app.include_router(backend.register(ctx), prefix="/api/p/intraday")
    return TestClient(app)


# ---------------------------------------------------------------- 测试数据

def _snap(ts: str, all_v: float, boards: Dict[str, Any]) -> Dict[str, Any]:
    return {"ts": ts, "all": all_v, "boards": boards}


TRACK_DOC = {
    "meta": {"schema_version": "1.0.0", "document_type": "amv_live_sector_track",
             "generated_at": "2026-10-23 15:00:56", "producer": "engine",
             "source": "qmt:full-tick+v9(tick_feed)"},
    "dates": {
        # 旧一日：混用无横杠日期键，必须被 _dkey 归一后正确排序
        "20261022": [_snap("2026-10-22 09:30:29", 0.0, {"881101.TI": {"name": "种植业", "amv_pct": 1.0}})],
        "2026-10-23": [
            # 盘前 08:59 点：必须被 09:30–15:00 过滤掉（旧端 _stSnaps 同口径）
            _snap("2026-10-23 08:59:10", -9.9, {"881101.TI": {"name": "种植业", "amv_pct": -9.9}}),
            _snap("2026-10-23 09:30:29", 1.0, {
                "881101.TI": {"name": "种植业", "amv_pct": 2.0, "chg_e": 1.5, "chg_w": 1.2, "amt_share": 0.6},
                "881102.TI": {"name": "养殖业", "amv_pct": -1.0, "chg_e": -0.5, "chg_w": -0.3, "amt_share": 0.3},
                "881103.TI": {"name": "电机", "amv_pct": 3.5, "chg_e": 2.2, "chg_w": 2.0, "amt_share": 0.1},
            }),
            _snap("2026-10-23 11:30:00", 1.5, {
                "881101.TI": {"name": "种植业", "amv_pct": 2.5, "chg_e": 1.6, "chg_w": 1.3, "amt_share": 0.61},
                "881102.TI": {"name": "养殖业", "amv_pct": -2.0, "chg_e": -0.6, "chg_w": -0.4, "amt_share": 0.29},
                "881103.TI": {"name": "电机", "amv_pct": 4.0, "chg_e": 2.4, "chg_w": 2.1, "amt_share": 0.10},
            }),
            # 收盘后 15:30 点：同样被过滤
            _snap("2026-10-23 15:30:00", 2.0, {"881101.TI": {"name": "种植业", "amv_pct": 9.9}}),
        ],
    },
}


# ---------------------------------------------------------------- 1. manifest

def test_manifest_valid() -> None:
    m = validate_manifest(_manifest(), PLUGIN_DIR)
    assert m.id == "intraday"
    assert m.version == "0.1.7"   # 0.1.2 盘中模式隔离+六指数叠加（-29/-30/-31/-32）；0.1.3 AMV 预测实时源（盘中按需刷新）；0.1.4 成交额预估分母/行情全A 基准线/正负对比分组/打开首屏与收盘定格（-34/-35/-39/-40）；0.1.5 恐慌图叠全A 880008 涨跌背景层 + 光标读数卡（-58）；0.1.7 板块行右键菜单（-74）+ 成交额区去说明（-76/-77）+ 删除 AMV 预测浮层（-73）
    assert m.kind == "domain"
    assert m.enabled is True
    assert m.backend.mount == "plugin", "本插件不挂 root：根路径由 compat / compat_sector 持有"


def test_manifest_card() -> None:
    cards = _manifest()["frontend"]["cards"]
    assert [c["id"] for c in cards] == ["intraday.main"]
    assert cards[0]["title"] == "盘中"
    assert cards[0]["slot"] == "main"
    assert isinstance(cards[0]["order"], int)


def test_manifest_panel_whitelist() -> None:
    """声明的每个 panel 名都必须真在核心白名单里（防「自己声明自己」绕过）。"""
    m = validate_manifest(_manifest(), PLUGIN_DIR)
    assert m.panel_read == ("amv_live_sector_track",)
    for name in m.panel_read:
        assert name in core_paths.READABLE_PANEL, "data.panel 声明了白名单外的名字：%s" % name


def test_manifest_read_only() -> None:
    m = validate_manifest(_manifest(), PLUGIN_DIR)
    assert m.data_write == (), "v1 插件 data.write 必须为空"
    assert m.state_write == () and m.misc_write == ()
    assert m.data_read == () and m.json_read == () and m.lib_read == ()
    # 0.1.5（标注 58）：冰点卡要叠全A 880008 日涨跌背景层，quotes 由 False 改 True。
    #   取数仍走 core 持有的 eltdx 单例（ctx.quotes.daily_bars），插件自己不连行情源、
    #   不落盘、不做全市场循环；feed / paper / ops_run 保持关闭。
    assert m.quotes_read is True and m.feed_read is False and m.paper_read is False
    assert m.ops_run is False


# ---------------------------------------------------------------- 2. 路由

def test_register_returns_router() -> None:
    router = backend.register(FakeCtx())
    assert isinstance(router, APIRouter)
    assert sorted(r.path for r in router.routes) == ["/health", "/index-series", "/summary"]


def test_health_shape() -> None:
    body = _client(FakeCtx()).get("/api/p/intraday/health").json()
    assert body["ok"] is True and body["read_only"] is True
    assert body["card"] == "intraday.main"
    assert body["refresh_every_ms"] == 20000
    assert "/api/panic" in body["sources"]
    assert body["write_ops"] == []


# ---------------------------------------------------------------- 3. /summary

def test_summary_picks_latest_date_and_filters_session() -> None:
    body = _client(FakeCtx({"amv_live_sector_track": TRACK_DOC})).get("/api/p/intraday/summary").json()
    assert body["ok"] is True
    assert body["date"] == "2026-10-23", "混用日期键时按去横杠比较取最新"
    assert body["snaps"] == 2, "08:59 / 15:30 两个盘外点必须被过滤"
    assert [p["t"] for p in body["all_series"]] == ["09:30", "11:30"]
    assert body["first_all"] == 1.0 and body["last_all"] == 1.5
    assert body["all_delta"] == 0.5
    assert body["last_ts"] == "2026-10-23 11:30:00"
    assert body["boards_total"] == 3


def test_summary_rel_is_amv_minus_all() -> None:
    body = _client(FakeCtx({"amv_live_sector_track": TRACK_DOC})).get("/api/p/intraday/summary").json()
    top = body["leaders"][0]
    assert top["code"] == "881103.TI", "超额最高=电机(4.0-1.5)"
    assert top["rel"] == pytest.approx(2.5)
    assert body["leaders"][1]["code"] == "881101.TI" and body["leaders"][1]["rel"] == pytest.approx(1.0)
    assert body["laggards"][0]["code"] == "881102.TI", "落后榜按 rel 升序"
    assert body["laggards"][0]["rel"] == pytest.approx(-3.5)
    assert body["strong"] == 2 and body["weak"] == 1


def test_summary_top_clamped() -> None:
    doc = {"dates": {"20261023": [_snap("2026-10-23 10:00:00", 0.0, {
        "c%02d.TI" % i: {"name": "b%d" % i, "amv_pct": float(i)} for i in range(30)})]}}
    body = _client(FakeCtx({"amv_live_sector_track": doc})).get(
        "/api/p/intraday/summary?top=99").json()
    assert body["top"] == 20 and len(body["leaders"]) == 20, "top 上限 20，防一次吐全表"
    # top=0/缺省 → 回落默认 6（不把 0 当「取全部」，也不当 1）
    body0 = _client(FakeCtx({"amv_live_sector_track": doc})).get(
        "/api/p/intraday/summary?top=0").json()
    assert body0["top"] == backend.TOP_DEFAULT and len(body0["leaders"]) == backend.TOP_DEFAULT


def test_summary_explicit_date() -> None:
    body = _client(FakeCtx({"amv_live_sector_track": TRACK_DOC})).get(
        "/api/p/intraday/summary?date=2026-10-22").json()
    assert body["date"] == "20261022" and body["snaps"] == 1


def test_summary_missing_file_is_404() -> None:
    res = _client(FakeCtx({})).get("/api/p/intraday/summary")
    assert res.status_code == 404
    assert "不存在" in res.json()["detail"]


def test_summary_bad_file_is_503() -> None:
    res = _client(FakeCtx(error=ValueError("bad json"))).get("/api/p/intraday/summary")
    assert res.status_code == 503


def test_summary_without_dates_is_ok_false() -> None:
    body = _client(FakeCtx({"amv_live_sector_track": {"meta": {}}})).get(
        "/api/p/intraday/summary").json()
    assert body["ok"] is False and "dates" in body["reason"]


def test_summary_off_session_only_is_ok_false() -> None:
    doc = {"dates": {"20261023": [_snap("2026-10-23 08:59:00", 1.0, {})]}}
    body = _client(FakeCtx({"amv_live_sector_track": doc})).get(
        "/api/p/intraday/summary").json()
    assert body["ok"] is False and body["snaps"] == 0, "盘外点全被过滤时如实报无数据"


def test_summary_undeclared_panel_is_500() -> None:
    ctx = FakeCtx({"amv_live_sector_track": TRACK_DOC}, panel_read=())
    res = _client(ctx).get("/api/p/intraday/summary")
    assert res.status_code == 500 and "manifest.data.panel" in res.json()["detail"]


def test_summary_nan_treated_as_missing() -> None:
    doc = {"dates": {"20261023": [
        _snap("2026-10-23 09:30:00", float("nan"), {"881101.TI": {"name": "x", "amv_pct": float("inf")}}),
        _snap("2026-10-23 09:31:00", 0.0, {"881101.TI": {"name": "x", "amv_pct": 1.0}}),
    ]}}
    body = _client(FakeCtx({"amv_live_sector_track": doc})).get("/api/p/intraday/summary").json()
    assert body["all_series"][0]["all"] is None, "nan 当缺值，不写进序列"
    assert body["first_all"] == 0.0, "first_all 取首个有效点（跳过 nan）"
    assert body["leaders"][0]["rel"] == pytest.approx(1.0)


# ---------------------------------------------------------------- 4. 静态禁令

def test_backend_no_forbidden_io() -> None:
    src = BACKEND_PY.read_text(encoding="utf-8")
    for token in ("sqlite3.connect", "xtquant", "xtdata", "TdxClient", "urllib.request.urlopen",
                  "requests.", "subprocess.", "json.dump(", ".write_text(", "os.replace("):
        assert token not in src, "backend.py 命中禁用写法：%s" % token
    assert "open(" not in src, "backend.py 不得自己开文件句柄（只走 ctx.panel_json）"


BANNED_JS = (
    ".write_text(", ".write_bytes(", ".mkdir(", ".touch(", ".unlink(", ".rmdir(", ".symlink_to(",
    "os.remove(", "os.unlink(", "os.rename(", "os.replace(", "os.makedirs(", "shutil.rmtree(",
    "shutil.move(", "shutil.copy", "json.dump(", ".to_sql(", ".to_csv(", ".to_parquet(",
    "INSERT INTO", "INSERT OR", "UPDATE ", "DELETE FROM", "DROP TABLE", "CREATE TABLE",
    "requests.post", "requests.put", "requests.delete", "requests.patch",
    "urllib.request.urlopen", "httpx.post", "httpx.put", "subprocess.", "os.system(", "os.popen(",
    "Popen(",
)
OPEN_WRITE_RE = re.compile(r"""open\s*\([^)]{0,240}?,\s*["'](?:w|a|wb|ab|x|xb)["']""")


def _js() -> str:
    return FRONTEND_JS.read_text(encoding="utf-8")


def _js_code() -> str:
    """去掉注释后的 frontend.js（守门要看代码；注释里会出现「已删 XXX」这类字样）。"""
    src = _js()
    src = re.sub(r"/\*[\s\S]*?\*/", "", src)
    return re.sub(r"(?m)^\s*//.*$", "", src)


def test_frontend_no_write_tokens() -> None:
    src = _js()
    hits = [t for t in BANNED_JS if t in src]
    assert not hits, "frontend.js 命中门禁禁令：%s" % hits
    assert not OPEN_WRITE_RE.search(src)


def test_frontend_no_raw_hex() -> None:
    found = re.findall(r"#[0-9a-fA-F]{3,8}\b", _js())
    assert not found, "frontend.js 出现裸 hex 色值：%s" % found[:8]


def test_frontend_no_third_party_imports() -> None:
    """零外部依赖：只允许相对路径的**同插件**子模块，不得有裸包名 / URL / 构建产物。

    2026-10-25 C 单：frontend.js 从 599 行单文件拆成 frontend/{format,caliber,sector,card.css}.js，
    于是「零 import」这条旧断言（v0.1.0 契约）不再成立，改为守真正想要的性质：
    不引入任何第三方依赖、不拉 CDN、不依赖构建。
    """
    src = _js()
    specs = re.findall(r"^\s*import\s[^'\"]*['\"]([^'\"]+)['\"]", src, re.M)
    for spec in specs:
        assert spec.startswith('./'), "frontend.js 只允许相对路径导入，实测到：%s" % spec
    # 子模块同样不得拉外部依赖
    for sub in (PLUGIN_DIR / "frontend").glob("*.js"):
        body = sub.read_text(encoding="utf-8")
        for spec in re.findall(r"^\s*import\s[^'\"]*['\"]([^'\"]+)['\"]", body, re.M):
            assert spec.startswith('./'), "%s 出现非相对导入：%s" % (sub.name, spec)
        assert "http://" not in body and "https://" not in body, "%s 出现外部 URL" % sub.name


def test_frontend_submodules_exist() -> None:
    """拆分后的子模块必须真实存在（防「import 写了、文件没落」的静默 404）。"""
    for name in ("format.js", "caliber.js", "sector.js", "card.css.js"):
        p = PLUGIN_DIR / "frontend" / name
        assert p.is_file(), "缺子模块 %s" % name
        assert p.stat().st_size > 200, "子模块 %s 内容为空壳" % name


def test_frontend_no_global_writes() -> None:
    assert not re.search(r"(^|\s)(?:window|globalThis)\.[A-Za-z_$]+\s*=", _js())


def test_frontend_uses_theme_tokens() -> None:
    src = _js()
    for token in ("var(--up)", "var(--down)", "var(--warn)", "var(--danger)", "var(--dim)",
                  "var(--accent)", "var(--card2)", "var(--line-soft)"):
        assert token in src, "缺少主题 token %s" % token


def test_frontend_declares_refresh_and_sources() -> None:
    src = _js()
    assert "refreshEvery: REFRESH_MS" in src
    assert "const REFRESH_MS = 20000;" in src
    for ep in ("/api/amv_live.json", "/api/panic", "/api/breadth_880.json",
               "/api/sector_intraday.json?codes=", "'/summary'"):
        assert ep in src, "缺少数据端点 %s" % ep
    # 只走 ctx.api，不裸 fetch
    assert "fetch(" not in src, "卡片只允许 ctx.api.get，禁止裸 fetch"


def test_close_freeze_window() -> None:
    """N20261027-234539-35：16:10 之后（收盘自动取数完成）不再自动取数。

    主人原话：「收盘期间就没必要刷新了，如果打开时间是 16:10（收盘自动取数之后）
    就直接取收盘后的数值进行一次最后的计算」。
    窗口口径：交易日 09:30–16:10 才自动取数；其余时段（盘前 / 收盘后 / 周末）跳过，
    首挂载仍取一次，手动「刷新」不受影响。
    """
    proc = subprocess.run(["node", str(PLUGIN_DIR / "tests" / "js" / "close_freeze_window.mjs")],
                          capture_output=True, text=True, timeout=120, cwd=str(ROOT))
    assert proc.returncode == 0, proc.stderr[-1500:]
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["pre"] is False, "盘前 09:00 不该自动取数"
    assert out["open"] is True, "盘中 10:00 要自动取数"
    assert out["close1510"] is True, "15:10 还在收盘取数窗口（15:00–16:10），要刷"
    assert out["tail"] is True, "16:00 仍在窗口内"
    assert out["after"] is False, "16:30 已过 16:10，必须停"
    assert out["late"] is False, "21:30 更不该刷"
    assert out["weekend"] is False, "周六不自动取数"
    assert out["frozenAfter"] is True and out["frozenOpen"] is False, "卡头「收盘定格」标注只看 16:10"


def test_frontend_does_not_pull_full_track() -> None:
    """板块轮动·当天需要**全量** sector_track（四象/网格的 x/y 与当日序列都在里面），
    2026-10-25 C 单已按旧页口径改为拉全量，但必须带 TTL 缓存。

    守的两条性质（比「不许拉」更接近真正想要的）：
      ① 全量拉取有 TTL，不得每轮 refresh 都重拉（2.7MB × 每 20s = 每分钟 8MB 无效负载）；
      ② 不得出现第二处取数点（旧页就一处 loadSectorTrack）。
    """
    src = _js()
    assert "const TRACK_TTL_MS = " in src, "全量轨迹必须有 TTL 常量"
    assert re.search(r"const TRACK_TTL_MS = \d+;", src), "TTL 必须是字面数字"
    assert src.count("'/api/sector_track.json'") == 1, "全量轨迹只允许一处取数"
    # TTL 必须真的被用上（读了常量却没判断 = 白写）
    assert "Date.now() - S.trackAt < TRACK_TTL_MS" in src, "TTL 必须参与缓存判断"


def test_frontend_panic_chart_contract() -> None:
    """旧端 4601 口径禁改项：单轴 0–100、跌停分位反向、冰点阈值 10。"""
    src = _js()
    assert "const ICE_THRESHOLD = 10;" in src
    assert "100 - x.q_limit_down_count" in src
    assert "禁加均线" in src or "禁加" in src


def test_trace_card_and_amv_summary_removed() -> None:
    """2026-10-26 主人看过版式后批两处「重复的」，两块必须真从卡里摸掉：

    ① 【板块轨迹明细】整卡 —— 领先/落后双榜 ≈ 日内演化右榜，逐码脉冲小图 ≈ 板块轮动网格视图；
    ② 【持仓实时状态】上段的 AMV 摘要 —— 与卡头 AMV 徒标、日内演化前重。
    要求：代码可留（trackHtml / pulseHtml / amvHtml 函数还在），但**不得再进编排**。
    """
    src = _js_code()
    assert "secTrace" not in src, "板块轨迹明细还在编排里"
    assert "it-trace-host" not in src, "轨迹卡宿主容器还在"
    assert "板块轨迹明细" not in src, "轨迹卡区头还在"
    assert "S.el.traceBody" not in src, "render 里还在写轨迹卡"
    # 持仓卡不再拼 AMV 摘要
    assert "amvHtml(S.amv)" not in src, "持仓卡还在渲染 AMV 摘要段"
    assert "上段 AMV 摘要为 v2 增量" not in src, "持仓卡区头还挂着 AMV 摘要的说明"
    # 函数本体保留（可回退）
    for fn in ("function trackHtml(", "function pulseHtml(", "function amvHtml("):
        assert fn in src, "函数 %s 不应删（可保留可回退）" % fn
    # 块序：日内演化之后直接接研究
    assert "secEvo, secResearch, secHold, secAudit, src" in src, "块序应变成 evo → 研究 → 持仓 → 审计"


def test_sector_click_goes_to_research() -> None:
    """2026-10-26 主人拍「统一」：四象的标签 + 网格格不再开板块详情，与日内演化右榜同行为。

    守住三条：
      ① sector.js 里零 `data-open-amv`（一律不弹详情卡）；
      ② 标签 `.st-ql` 与网格格 `.st-cell` 带 `data-act="st-goto"`，点击走 hooks.pickBoard；
      ③ 气泡的钉住交互不被破坏（`.st-bub, .st-line` 那段 pin 逻辑仍在，且 st-goto 提前 return）。
    """
    sec = (PLUGIN_DIR / "frontend" / "sector.js").read_text(encoding="utf-8")
    sec = re.sub(r"/\*[\s\S]*?\*/", "", sec)
    sec = re.sub(r"(?m)^\s*//.*$", "", sec)
    assert "data-open-amv" not in sec, "sector 还带着 data-open-amv（点下去会弹板块详情）"
    assert sec.count('data-act="st-goto"') >= 2, "标签与网格格都要改委托（现在 %d 处）" % sec.count('data-act="st-goto"')
    assert "st.hooks && st.hooks.pickBoard" in sec, "没接 pickBoard 出口"
    assert ".st-bub, .st-line" in sec, "气泡钉住逻辑不能捣"
    # st-goto 必须在钉住分支之前 return，否则点标签会先去钉住
    h0 = sec.index("host.addEventListener('click'")
    h1 = sec.index("}, opt);", h0)
    handler = sec[h0:h1]
    assert "st-goto" in handler and ".st-bub" in handler, "同一个 click 处理里应同时有 noread 两分支"
    assert handler.index("st-goto") < handler.index(".st-bub"), "st-goto 分支未先于钉住分支"
    assert "return" in handler[handler.index("st-goto"):handler.index(".st-bub")], \
        "st-goto 分支要 return，别接着去钉住"
    # 编排：两个模块共用一个 pickBoard
    fe = _js()
    assert "const pickBoard = (code) =>" in fe
    assert "sector.createState(wrap, sectorBody, { pickBoard" in fe
    assert "pickBoard: pickBoard" in fe


# ---------------------------------------------------------------- 5. /index-series

#: thermometer 产物名（生产方 tools/index_880008_daily.py）
THERMO_KEY = ("thermometer", "index_880008_series")


def _reset_all_a_cache() -> None:
    """模块级 120s 缓存跨用例共用：每个用例动手前先清，否则①的结果会喂给②。"""
    backend._ALL_A_CACHE.update(at=0.0, doc=None)


def _thermo_doc(closes: List[float]) -> Dict[str, Any]:
    """产物夹具（结构与真文件一致：meta + series，series 行含 close/source）。"""
    dates = ["202601%02d" % (i + 1) for i in range(len(closes))]
    return {
        "meta": {"merged_last": dates[-1], "rows": len(closes), "overlap_max_diff": 0.0},
        "series": [{"date": d, "open": c, "high": c, "low": c, "close": c,
                    "amount": 1.0e9, "vol": 1, "source": "vipdoc"}
                   for d, c in zip(dates, closes)],
    }


def _eltdx_bars(closes: List[float]) -> List[Any]:
    """eltdx 日 K 替身：`.time` 是 datetime，收盘价在 `.close_price`。"""
    return [SimpleNamespace(time=datetime(2026, 11, 2 + i), close_price=c)
            for i, c in enumerate(closes)]


def test_index_series_prefers_thermometer_product() -> None:
    """① 有产物：用产物末 n 根算 pct，**一次都不打 quotes**。"""
    _reset_all_a_cache()
    closes = [100.0 + i for i in range(25)]
    ctx = FakeCtx(outputs={THERMO_KEY: _thermo_doc(closes)})
    body = _client(ctx).get("/api/p/intraday/index-series?n=20").json()

    assert body["ok"] is True and body["code"] == "sh880008"
    assert body["source"] == "thermometer:index_880008"
    assert [r["date"] for r in body["rows"]] == ["202601%02d" % (i + 6) for i in range(20)]
    # 首行要能算出涨幅：说明多取了一根前收，口径与旧实现（pct=close/prev_close-1）一致
    assert body["rows"][0]["close"] == 105.0
    assert body["rows"][0]["pct"] == pytest.approx(round((105.0 / 104.0 - 1) * 100.0, 2))
    assert body["rows"][-1]["close"] == 124.0
    assert body["rows"][-1]["pct"] == pytest.approx(round((124.0 / 123.0 - 1) * 100.0, 2))
    assert ctx.quotes.calls == [], "有产物时不得再打 eltdx（省掉每轮行情请求）"
    assert ctx.output_calls == [THERMO_KEY]
    assert body["reason"] == ""


def test_index_series_without_product_falls_back_to_quotes() -> None:
    """② 产物缺失：回退 eltdx 路径（结构/字段/口径不变），source 标 eltdx。"""
    _reset_all_a_cache()
    ctx = FakeCtx(outputs={}, quotes=FakeQuotes(_eltdx_bars([100.0, 101.0, 100.0])))
    body = _client(ctx).get("/api/p/intraday/index-series?n=20").json()

    assert body["ok"] is True and body["code"] == "sh880008"
    assert body["source"] == "eltdx:sh880008"
    assert [r["date"] for r in body["rows"]] == ["20261102", "20261103", "20261104"]
    assert body["rows"][0]["pct"] is None, "首行无前收，pct 留空（与旧实现一致）"
    assert body["rows"][1]["pct"] == pytest.approx(1.0)
    assert body["rows"][-1]["pct"] == pytest.approx(round((100.0 / 101.0 - 1) * 100.0, 2))
    assert ctx.quotes.calls == [{"code": "sh880008", "n": 80}], "回退仍走原取数口"


def test_index_series_bad_product_falls_back() -> None:
    """③ 产物结构不符/空：当缺失处理，一律回退 eltdx（不报 500、不造数）。"""
    bad_docs: List[Any] = [
        {"meta": {"merged_last": "20261106"}},              # 没有 series/bars
        {"series": []},                                       # 空序列
        {"series": [{"date": "20261106"}]},                  # 行里没有 close
        {"series": [{"date": "20261106", "close": 0.0}]},   # close 非正数（结构脏）
        ["oops"],                                              # 顶层不是对象
    ]
    for bad in bad_docs:
        _reset_all_a_cache()
        ctx = FakeCtx(outputs={THERMO_KEY: bad}, quotes=FakeQuotes(_eltdx_bars([120.0, 121.0])))
        body = _client(ctx).get("/api/p/intraday/index-series?n=20").json()
        assert body["ok"] is True and body["source"] == "eltdx:sh880008", bad
        assert body["rows"][-1]["close"] == 121.0, bad
        assert len(ctx.quotes.calls) == 1, bad


def test_index_series_no_output_read_channel_falls_back() -> None:
    """旧 core 没有 output_read（属性不存在/不可调）→ 直接走回退，不炸。"""
    class _NoOutputCtx(FakeCtx):
        output_read = None  # type: ignore[assignment]

    _reset_all_a_cache()
    ctx = _NoOutputCtx(quotes=FakeQuotes(_eltdx_bars([100.0, 102.0])))
    body = _client(ctx).get("/api/p/intraday/index-series?n=20").json()
    assert body["ok"] is True and body["source"] == "eltdx:sh880008"
    assert len(ctx.quotes.calls) == 1


def test_index_series_both_sources_empty_is_ok_false() -> None:
    """两路都取不到：ok=false + reason，不静默返回空序列当有效。"""
    _reset_all_a_cache()
    ctx = FakeCtx(outputs={}, quotes=FakeQuotes([]))
    body = _client(ctx).get("/api/p/intraday/index-series?n=20").json()
    assert body["ok"] is False and body["rows"] == []
    assert "返回空" in body["reason"] and body["source"] == ""


def test_index_series_ignores_product_intraday() -> None:
    """盘中就算产物在，也不得用——产物是收盘链 16:10 写的，盘中最后一根是昨收，
    拿它会把「当日涨跌」滞后一天（主人 2026-11-07 提醒：不能拿昨天当今天）。"""
    _reset_all_a_cache()
    ctx = FakeCtx(outputs={THERMO_KEY: _thermo_doc([100.0 + i for i in range(25)])},
                  quotes=FakeQuotes(_eltdx_bars([200.0, 202.0])),
                  postclose=False)
    body = _client(ctx).get("/api/p/intraday/index-series?n=20").json()
    assert body["source"] == "eltdx:sh880008", "盘中必须走实时源"
    assert body["rows"][-1]["close"] == 202.0
    assert ctx.output_calls == [], "盘中不该去读盘后产物"
    assert len(ctx.quotes.calls) == 1
