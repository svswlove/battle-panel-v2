# -*- coding: utf-8 -*-
"""情绪温度计 · 四维拆解测试（2026-10-29）。

守四件事：
1. **分位口径与 emotion 同式**：把 `dims.pct_rank` 与 `emotion.engine.pct_rank` 在随机样本上逐点比对
   （跨插件不许 import 生产代码，所以本插件重写了一份同公式实现；这个测试就是防两份走散的那根钉子）。
2. **方向统一**：跌停家数/炸板率这类「越高越冷」的项必须取 `1 − 分位`，其余直接用分位。
3. **有效项归一**：某子项缺失/样本不足 → 跳过并按有效项平均，**不用 0 顶替**；整维无有效项 → None。
4. **真实接口**：`/dims` 在真实 core 上跑**库里最后一天**的数。库里每天收盘后会追加一行，
   所以本组全部用**相对断言**：`asof == 库内最后交易日`、`sentiment_rows == 实读行数`、
   各维分数与「用同一份库内数据独立算出的值」比——不锁固定日期/行数/分数。
"""
from __future__ import annotations

import importlib.util
import json
import random
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
DIMS = PLUGIN_DIR / "dims.py"
DB_PRESENT = (paths.db_path("track_pool.db") or Path("")).is_file()
needs_db = pytest.mark.skipif(not DB_PRESENT, reason="track_pool.db 不在本机")
#: 主力资金历史序列（data/panel，生产脚本 夏天-activity/工具/build_index_flow_series.py 写入）
FLOW_SERIES = Path(paths.DATA_DIR) / "panel" / "index_flow_series_880008.json"
HAS_FLOW_SERIES = FLOW_SERIES.is_file()


def _db_sentiment() -> list:
    """直读 track_pool.db 的 sentiment 收盘行（独立于插件实现，用来算当日的期望值）。"""
    import sqlite3

    con = sqlite3.connect("file:%s?mode=ro" % paths.db_path("track_pool.db").as_posix(), uri=True)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(
            "SELECT trade_date, up_cnt, down_cnt, zt_total, dt_total, zt_ever, height, lb2plus,"
            " promotion_2b FROM sentiment WHERE ts='close' ORDER BY trade_date")]
    finally:
        con.close()


def _rank(pool, value, window=250, min_sample=5):
    """独立实现的分位（同 emotion.engine.pct_rank：窗口含当日、并列半分、样本不足 → None）。"""
    if value is None:
        return None
    sample = [x for x in pool[-window:] if x is not None]
    if len(sample) < min_sample:
        return None
    return (sum(1 for x in sample if x < value) + 0.5 * sum(1 for x in sample if x == value)) / len(sample)


def _band(score):
    """极冷 <25 / 偏冷 25–40 / 中性 40–55 / 偏热 55–70 / 过热 ≥70（与 dims.DIM_BANDS 同口径）。"""
    for edge, label in ((70, "过热"), (55, "偏热"), (40, "中性"), (25, "偏冷")):
        if score >= edge:
            return label
    return "极冷"


def _mean_score(oriented):
    """有效项等权平均（缺失跳过）；无有效项 → None。"""
    vals = [o for o in oriented if o is not None]
    return None if not vals else round(sum(vals) / len(vals) * 100.0, 1)


def _dims():
    """按路径加载 dims.py（与 core 加载插件 backend 的方式一致）。"""
    spec = importlib.util.spec_from_file_location("_thm_dims_test", DIMS)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------- 1. 与 emotion 同式

def test_pct_rank_matches_emotion_engine():
    """随机样本逐点比对：本插件的分位实现必须与 emotion.engine.pct_rank 完全一致。"""
    from app.plugins.emotion import engine as emo_engine

    mod = _dims()
    rng = random.Random(20261029)
    for _ in range(40):
        n = rng.randint(5, 70)
        pool = [rng.choice([None, rng.randint(0, 60)]) for _ in range(n)]
        value = rng.choice([None, rng.randint(0, 60)])
        for window in (5, 20, 60, 250):
            mine = mod.pct_rank(pool, value, window)
            theirs = emo_engine.pct_rank(pool[-window:], value)
            assert mine == theirs, (window, value, n, mine, theirs)


def test_pct_rank_needs_min_sample():
    mod = _dims()
    assert mod.pct_rank([1, 2, 3, 4], 4) is None          # 样本 4 < 5
    assert mod.pct_rank([1, 2, 3, 4, 5], 5) is not None
    assert mod.pct_rank([None, None, None, None, None], 1) is None


# ---------------------------------------------------------------- 2/3. 方向与归一

def test_band_edges():
    mod = _dims()
    assert mod.band_of(0) == "极冷"
    assert mod.band_of(24.9) == "极冷"
    assert mod.band_of(25) == "偏冷"
    assert mod.band_of(40) == "中性"
    assert mod.band_of(55) == "偏热"
    assert mod.band_of(70) == "过热"
    assert mod.band_of(None) is None


def test_dim_averages_used_items_only():
    """有效项等权平均；缺失项跳过（不是按 0 算），并在 missing 里列原因。"""
    mod = _dims()
    items = [
        mod._item("a", "A", 1, direction="high_hot", pct=0.90, base=58, source="t"),
        mod._item("b", "B", 1, direction="low_hot", pct=0.10, base=58, source="t"),   # 反向 → 0.90
        mod._item("c", "C", None, direction="high_hot", pct=None, base=None, source="t",
                  used=False, why="样本不足"),
    ]
    dim = mod._dim("x", "测试", items)
    assert dim["n_used"] == 2 and dim["n_total"] == 3
    assert dim["score"] == pytest.approx(90.0)        # (0.9 + 0.9)/2 × 100
    assert dim["band"] == "过热"
    assert dim["missing"] == [{"key": "c", "label": "C", "why": "样本不足"}]


def test_dim_without_items_is_none_not_zero():
    mod = _dims()
    items = [mod._item("a", "A", None, direction="high_hot", pct=None, base=None,
                       source="t", used=False, why="缺")]
    dim = mod._dim("x", "测试", items)
    assert dim["score"] is None and dim["band"] is None
    assert dim["n_used"] == 0


def test_orientation_inverts_low_hot():
    mod = _dims()
    it = mod._item("dt", "跌停", 42, direction="low_hot", pct=0.92, base=58, source="t")
    assert it["percentile"] == 0.92
    assert it["oriented"] == pytest.approx(0.08)


# ---------------------------------------------------------------- 4. 真实接口

@pytest.fixture(scope="module")
def client():
    with TestClient(create_app()) as c:
        yield c


@needs_db
def test_dims_endpoint_today(client):
    """/dims 库内最后一日：asof/行数跟库走，分数与「同一份库内数据独立算出」的值比。

    库里每天收盘追加一行，所以不锁固定日期/行数/分数——锁了第二天必红。
    """
    r = client.get("/api/p/thermometer/dims", params={
        "promo_rate": 0.4, "promo_base": 5,
        "panic_median": -2.5969, "panic_median_q": 5.2,
        "panic_amount": 0.9558, "panic_amount_q": 33.6,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    rows = _db_sentiment()
    assert rows, "库里没有 sentiment 收盘行"
    asof, n = str(rows[-1]["trade_date"]), len(rows)
    window = min(250, n)
    assert body["asof"] == asof, "asof 必须是库内最后交易日"
    assert body["sentiment_rows"] == n, "行数必须等于实读行数（库每天追加）"
    assert body["sentiment_window"] == window, "窗口 = min(250, 行数) 降级"
    dims = {d["key"]: d for d in body["dims"]}
    assert list(dims) == ["breadth", "relay", "capital", "position"]

    # 广度：up_ratio / zt_total（高冷）/ dt_total（低热→1−分位）/ 全A中位（上游 250 日基）
    up = [None if (x["up_cnt"] is None or x["down_cnt"] is None or (x["up_cnt"] + x["down_cnt"]) == 0)
          else x["up_cnt"] / (x["up_cnt"] + x["down_cnt"]) for x in rows]
    zt = [x["zt_total"] for x in rows]
    dt = [x["dt_total"] for x in rows]
    b = dims["breadth"]
    want = _mean_score([_rank(up, up[-1], window), _rank(zt, zt[-1], window),
                        None if _rank(dt, dt[-1], window) is None else 1.0 - _rank(dt, dt[-1], window),
                        5.2 / 100.0])
    assert b["n_used"] == 4, "四子项齐（库三项 + 上游全A中位）"
    assert b["score"] == pytest.approx(want, abs=0.15), "广度分与独立算值不符：%s vs %s" % (b["score"], want)
    assert b["band"] == _band(b["score"])

    # 接力：height / lb2plus / 炸板率（只看分位、低热→1−分位）；晋级率分母 5 只 → 不参与
    height = [x["height"] for x in rows]
    lb2 = [x["lb2plus"] for x in rows]
    burst = [None if (a is None or not e) else (e - a) / e
             for a, e in zip(zt, [x["zt_ever"] for x in rows])]
    rl = dims["relay"]
    assert rl["n_used"] == 3
    assert rl["score"] == pytest.approx(
        _mean_score([_rank(height, height[-1], window), _rank(lb2, lb2[-1], window),
                     None if _rank(burst, burst[-1], window) is None
                     else 1.0 - _rank(burst, burst[-1], window)]), abs=0.15)
    promo = [it for it in dims["relay"]["items"] if it["key"] == "promotion"][0]
    assert promo["used"] is False and "样本不足" in promo["why"]
    assert promo["denominator"] == 5

    assert "%d/4 项参与" % dims["capital"]["n_used"] in dims["capital"]["note"]
    assert "两融已移出" in dims["capital"]["note"]
    assert "margin" not in [it["key"] for it in dims["capital"]["items"]]
    cap = {it["key"]: it for it in dims["capital"]["items"]}
    # 主力资金：正常路径 = L2 历史序列（507 条）→ 占成交 % 取 250 日分位；序列新鲜才参与打分
    mr = cap["main_ratio"]
    assert mr["raw"] is not None and mr["unit"] == "%"
    assert mr["asof"] is not None
    if HAS_FLOW_SERIES:
        assert mr["source_kind"] == "series"
        assert mr["base"] == 250 and 0.0 <= mr["percentile"] <= 1.0
        assert mr["used"] is (mr["asof"] == body["asof"]), "序列新鲜才参与；不新鲜只展示分位"
        assert "L2 序列" in (mr["text"] or "") and "5 日占成交" in (mr["text"] or "")
        assert len(mr["history"]) == 5
    else:
        assert mr["source_kind"] == "live" and mr["percentile"] is None
        assert "缺分位" in mr["why"]
    # 龙虎榜：全市场净买额（hithink 缓存逐日追加，只比形状，不锁具体亿位）
    assert cap["dragon"]["raw"] is not None and cap["dragon"]["base"] >= 5
    assert 0.0 <= cap["dragon"]["percentile"] <= 1.0
    # 北向：官方停披露日频金额，不填 0
    assert cap["northbound"]["used"] is False and "停披露" in cap["northbound"]["why"]
    assert cap["northbound"]["raw"] is None
    # 分数 = 有效项 oriented 等权平均（缺失跳过、不用 0 顶替）
    used = [it for it in dims["capital"]["items"] if it["used"] and it["oriented"] is not None]
    assert dims["capital"]["n_used"] == len(used)
    assert (dims["capital"]["score"] is None) == (not used)
    if used:
        assert dims["capital"]["score"] == pytest.approx(
            round(sum(it["oriented"] for it in used) / len(used) * 100, 1))


@needs_db
def test_position_dimension_uses_880008(client):
    """位置维度：880008 全A（vipdoc 历史 + eltdx 尾部），三个子项参与、新鲜到 asof。"""
    body = client.get("/api/p/thermometer/dims").json()
    pos = [d for d in body["dims"] if d["key"] == "position"][0]
    assert pos["n_used"] == 3 and pos["score"] is not None
    assert "880008" in pos["note"]
    probe = body["position_probe"]
    assert probe["code"] == "880008"
    assert probe["rows"] > 3600 and probe["first"] == "20111019"
    assert probe["fresh"] is True and probe["last"] == body["asof"]
    # vipdoc 尾部日期会随盘后补齐往前走（2026-11-06 实测 20261102），只校验形状、不锁具体日
    assert re.fullmatch(r"\d{8}", str(probe["vipdoc_last"]))
    assert probe["overlap_max_diff"] == 0.0
    # 三个子项都参与、都落在 0–1 分位（点位方向随行情变，不锁正负）
    items = {it["key"]: it for it in pos["items"]}
    assert all(it["raw"] is not None and it["used"] is True for it in items.values())
    assert all(0.0 <= it["percentile"] <= 1.0 and it["base"] == 250 for it in items.values())
    assert pos["band"] is not None


@needs_db
def test_dims_without_upstream_params_marks_missing(client):
    """不传上游事实 → 全A中位/量能/晋级率标缺，仍能出分（只是有效项更少）。"""
    body = client.get("/api/p/thermometer/dims").json()
    dims = {d["key"]: d for d in body["dims"]}
    assert dims["breadth"]["n_used"] == 3        # 少全A中位
    # 资金：量能靠上游参数（没传）→ 缺；北向停披露 → 缺；剩下龙虎榜（+ 主力资金序列新鲜时）
    cap = dims["capital"]
    cap_used = [it for it in cap["items"] if it["used"] and it["oriented"] is not None]
    assert cap["n_used"] == len(cap_used) >= 1
    assert cap["score"] is not None
    assert dims["relay"]["n_used"] == 3          # 晋级率仍因样本不足不参与
    missing = {(m["dim"], m["key"]) for m in body["missing"]}
    assert ("breadth", "median_pct") in missing
    assert ("capital", "amount") in missing
    assert ("capital", "northbound") in missing


@needs_db
def test_manifest_declares_track_pool_read():
    """四维要读 sentiment + kline → 声明 track_pool.db；仍然只读、不写任何生产文件。"""
    m = load_manifest(PLUGIN_DIR)
    assert tuple(m.data_read) == ("track_pool.db",)
    assert tuple(m.panel_read) == ("panic_breadth", "index_flow_series_880008")
    assert tuple(m.data_write) == ()


def test_dims_module_has_no_write_or_http():
    src = DIMS.read_text(encoding="utf-8")
    for token in ("sqlite3.connect", "urllib", "requests.", "httpx", "subprocess",
                  "open(", ".write_text(", "json.dump(", "os.replace("):
        assert token not in src, token


# ---------------------------------------------------------------- 主力资金（① L2 序列 / ② eltdx 降级）

def _synthetic_flow_series(n=300, drift=0.01):
    """造一条 n 天的占成交序列（值递增 → 末值最高，分位应到 1.0）。"""
    rows = [{"date": "2026%04d" % (i + 1), "zc_pct": round(i * drift - 1.0, 4),
             "main_net_yi": round(i * drift * 10, 4)} for i in range(n)]
    return {"rows": rows, "meta": {"n": n, "last": rows[-1]["date"]}, "latest": {}, "reason": ""}


def test_flow_series_percentile_uses_shared_pct_rank():
    """序列可用 → 占成交取 250 日分位（复用本模块 pct_rank，不另写一套），参与打分。"""
    mod = _dims()
    series = _synthetic_flow_series(300)
    zc = [r["zc_pct"] for r in series["rows"]]
    today = series["rows"][-1]["date"]
    out = mod.build_dims([{"trade_date": today}], {"rows": [], "meta": {}}, flow_series=series)
    cap = [d for d in out["dims"] if d["key"] == "capital"][0]
    item = [it for it in cap["items"] if it["key"] == "main_ratio"][0]
    assert item["source_kind"] == "series"
    assert item["raw"] == pytest.approx(zc[-1])
    assert item["base"] == 250
    assert item["percentile"] == pytest.approx(mod.pct_rank(zc, zc[-1], 250))
    assert item["percentile"] > 0.99                        # 末值在 250 日窗口里最高（(249+0.5)/250）
    assert item["used"] is True and item["why"] == ""
    assert item["asof"] == today
    assert [h["date"] for h in item["history"]] == [r["date"] for r in series["rows"][-5:]][::-1]
    assert cap["flow_source"] == "series" and cap["flow_asof"] == today


def test_flow_series_real_product_matches_its_own_rank():
    """真实产物：条数/末日与 meta 一致；本插件 pct_rank 重算的分位 = 文件自带的 zc_pct_rank_250。"""
    if not HAS_FLOW_SERIES:
        pytest.skip("index_flow_series_880008.json 不在本机")
    mod = _dims()
    got = mod.load_index_flow_series(FLOW_SERIES)
    doc = json.loads(FLOW_SERIES.read_text(encoding="utf-8"))
    assert got["rows"] and len(got["rows"]) == doc["meta"]["n"]
    assert got["rows"][0]["date"] == doc["meta"]["first"]
    assert got["rows"][-1]["date"] == doc["meta"]["last"]
    assert got["rows"][-1]["zc_pct"] == pytest.approx(doc["latest"]["zc_pct"])
    zc = [r["zc_pct"] for r in got["rows"]]
    assert mod.pct_rank(zc, zc[-1], 250) == pytest.approx(doc["latest"]["zc_pct_rank_250"], abs=1e-4)


def test_flow_series_missing_falls_back_and_marks_no_percentile(tmp_path):
    """序列读不到 → 退回实时 5 日，该项标「缺分位」，维度不整维空掉。"""
    mod = _dims()
    got = mod.load_index_flow_series(tmp_path / "nope.json")
    assert got["rows"] == [] and "不存在" in got["reason"]
    live = {"rows": [{"date": "20261030", "main_ratio": -1.716, "main_net": -1.4614e10}], "reason": ""}
    out = mod.build_dims([{"trade_date": "20261030"}], {"rows": [], "meta": {}},
                         flow_series=got, index_flow=live)
    cap = [d for d in out["dims"] if d["key"] == "capital"][0]
    item = [it for it in cap["items"] if it["key"] == "main_ratio"][0]
    assert item["source_kind"] == "live"
    assert item["raw"] == pytest.approx(-1.716)
    assert item["percentile"] is None and item["used"] is False
    assert "缺分位" in item["why"]
    assert "主力资金缺分位" in cap["note"]


def test_flow_series_stale_keeps_percentile_but_not_used():
    """序列最新日 != 情绪侧 asof → 分位照展示（标「仅供参考」）、不参与打分（写死在代码里）。"""
    mod = _dims()
    out = mod.build_dims([{"trade_date": "20261231"}], {"rows": [], "meta": {}},
                         flow_series=_synthetic_flow_series(300))
    cap = [d for d in out["dims"] if d["key"] == "capital"][0]
    item = [it for it in cap["items"] if it["key"] == "main_ratio"][0]
    assert item["percentile"] is not None and item["used"] is False
    assert item["stale"] is True
    assert "序列陈旧" in item["why"] and "仅供参考" in item["why"] and "不参与打分" in item["why"]
    assert "序列陈旧" in cap["note"] and cap["flow_stale"] is True
    # 不参与打分：资金维的有效项里没有它
    assert "main_ratio" not in [it["key"] for it in cap["items"] if it["used"]]


def test_parse_index_flow_series_handles_bad_doc():
    """走面板白名单时拿到的是已解析 dict：顶层不是对象 → 空 rows + reason（不抛）。"""
    mod = _dims()
    bad = mod.parse_index_flow_series([1, 2, 3])
    assert bad["rows"] == [] and "顶层不是对象" in bad["reason"]
    empty = mod.parse_index_flow_series({"series": []})
    assert empty["rows"] == [] and empty["reason"] == "序列为空"


def test_flow_series_short_window_has_no_percentile():
    """样本 <5 日 → 不给分位（复用 pct_rank 的最小样本阀），不拿单点值当分位。"""
    mod = _dims()
    series = {"rows": [{"date": "2026102%d" % i, "zc_pct": 1.0 * i, "main_net_yi": i}
                      for i in range(3)], "meta": {}, "latest": {}, "reason": ""}
    out = mod.build_dims([{"trade_date": "20261022"}], {"rows": [], "meta": {}}, flow_series=series)
    item = [it for d in out["dims"] if d["key"] == "capital"
            for it in d["items"] if it["key"] == "main_ratio"][0]
    assert item["percentile"] is None and item["used"] is False
    assert "不足 5 日" in item["why"]


def test_index_flow_fallback_shows_ratio_and_5day_compare():
    """降级路径（只有 eltdx 一次请求的 5 条）：主值 main_ratio、副值 main_net、缺分位、带 5 日对比。"""
    mod = _dims()
    flow = {"rows": [
        {"date": "20261030", "main_ratio": -1.716, "main_net": -1.4614e10, "amount": 8.516e11},
        {"date": "20261029", "main_ratio": -4.156, "main_net": -6.4676e10, "amount": 1.556e12},
        {"date": "20261028", "main_ratio": 1.588, "main_net": 2.5856e10, "amount": 1.628e12},
        {"date": "20261027", "main_ratio": -2.744, "main_net": -4.0084e10, "amount": 1.461e12},
        {"date": "20261026", "main_ratio": -1.442, "main_net": -2.3197e10, "amount": 1.609e12},
    ], "reason": ""}
    out = mod.build_dims([{"trade_date": "20261029"}], {"rows": [], "meta": {}}, index_flow=flow)
    cap = [d for d in out["dims"] if d["key"] == "capital"][0]
    item = [it for it in cap["items"] if it["key"] == "main_ratio"][0]
    assert item["raw"] == pytest.approx(-1.716)          # 主值 = 占成交 %
    assert item["unit"] == "%"
    assert item["percentile"] is None and item["used"] is False
    assert "不分位" not in item["why"] and "缺分位" in item["why"]
    assert item["asof"] == "20261030"                    # 取最新一条（含当日盘中）
    assert item["main_net_yi"] == pytest.approx(-146.14, abs=0.05)
    assert len(item["history"]) == 5 and item["history"][0]["date"] == "20261030"
    assert "5 日占成交" in (item["text"] or "") and "eltdx" in (item["text"] or "")
    assert "5日占成交" in (cap.get("flow_compare") or "")
    assert cap["flow_asof"] == "20261030" and cap["flow_source"] == "live"


def test_index_flow_missing_marks_item_missing_not_zero():
    """取数失败 → 该项标缺（raw=None），不填 0，不影响其它三项。"""
    mod = _dims()
    out = mod.build_dims([{"trade_date": "20261029"}], {"rows": [], "meta": {}},
                         index_flow={"rows": [], "reason": "eltdx 不可用"})
    cap = [d for d in out["dims"] if d["key"] == "capital"][0]
    item = [it for it in cap["items"] if it["key"] == "main_ratio"][0]
    assert item["raw"] is None and item["used"] is False
    assert "eltdx 不可用" in item["why"]
    assert "主力资金缺" in cap["note"]


def test_dragon_series_sums_net_value(tmp_path):
    """龙虎榜日值 = 当日全部股票 net_value 求和；缺 net_value 的股不计入但留计数。"""
    mod = _dims()
    cache = tmp_path / "xiaoshi_cache"
    cache.mkdir()
    day = {"data": {"data": {"stock_items": [
        {"net_value": 1.5e8}, {"net_value": -0.5e8}, {"net_value": None}, {"name": "无净额"},
    ]}}}
    (cache / "hithink_20261029_dragon.json").write_text(json.dumps(day), encoding="utf-8")
    (cache / "hithink_20261028_dragon.json").write_text(
        json.dumps({"data": {"data": {"stock_items": [{"net_value": 4.9e8}]}}}), encoding="utf-8")
    got = mod.load_dragon_series(cache)
    assert [r["date"] for r in got["rows"]] == ["20261028", "20261029"]
    assert got["rows"][-1]["net_sum"] == pytest.approx(1.0e8)
    assert got["rows"][-1]["n"] == 4 and got["rows"][-1]["n_net"] == 2


def test_dragon_series_missing_cache_is_empty(tmp_path):
    mod = _dims()
    got = mod.load_dragon_series(tmp_path / "nope")
    assert got["rows"] == [] and got["reason"]


# ---------------------------------------------------------------- 位置维度（880008 全A）


def _synthetic_rows(n=320, drift=0.0006, growth=0.0035):
    """造一条 320 天的指数日线（前 280 天慢涨、后 40 天加速），供位置子项算 MA 偏离与回撤。"""
    rows = []
    lvl = 5000.0
    for i in range(n):
        lvl *= (1.0 + drift + (growth if i > n - 40 else 0.0))
        rows.append({"date": "2026%04d" % (i + 1), "close": round(lvl, 2)})
    return rows


def test_position_dimension_uses_full_a_index():
    """880008 序列够长时三个子项都参与、250 日分位（不再用相关闸门）。"""
    mod = _dims()
    rows = _synthetic_rows()
    dim, items, probe, asof = mod.position_block({"rows": rows, "meta": {"merged_last": rows[-1]["date"]}})
    assert probe["code"] == "880008"
    assert probe["rows"] == 320 and probe["last"] == rows[-1]["date"]
    assert dim["n_used"] == 3 and dim["score"] is not None
    assert all(it["used"] is True and it["base"] == 250 for it in items)
    # 后段加速上涨 → 正偏离、回撤浅
    dev20 = [it for it in items if it["key"] == "dev20"][0]
    assert dev20["raw"] is not None and dev20["raw"] > 0
    assert asof == rows[-1]["date"]


def test_position_dimension_degraded_when_series_missing():
    mod = _dims()
    dim, items, probe, asof = mod.position_block(None)
    assert probe["rows"] == 0 and dim["score"] is None and dim["n_used"] == 0
    assert all(it["raw"] is None for it in items)


def test_position_dimension_short_series_is_missing_not_guessed():
    """根数不够（<300）→ 整维缺，不拿短序列凑值。"""
    mod = _dims()
    rows = _synthetic_rows(200)
    dim, items, probe, _asof = mod.position_block({"rows": rows, "meta": {}})
    assert dim["score"] is None and "仅 200 根" in probe["reason"]
    assert all(it["raw"] is None and it["used"] is False for it in items)


def test_position_stale_series_does_not_pass_as_today():
    """序列停在 10-22、情绪侧 asof 是 10-29 → 位置整维标缺（不拿旧点位冒充今日）。"""
    mod = _dims()
    rows = _synthetic_rows()
    rows[-1]["date"] = "20261022"
    out = mod.build_dims([{"trade_date": "20261029"}], {"rows": rows, "meta": {}})
    pos = [d for d in out["dims"] if d["key"] == "position"][0]
    assert out["position_probe"]["fresh"] is False
    assert pos["score"] is None and pos["n_used"] == 0
    assert "不拿旧点位冒充今日" in pos["note"]
    assert all(it["used"] is False and it["raw"] is not None for it in pos["items"])
