#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""M4 切流前 shadow 双跑工具：同一逻辑请求同时打旧端与新 8810，按域比对。

铁律：
- 只发 HTTP GET。不连库、不写生产数据、不改旧端口。
- 结果只落项目内 data/panel_v2/shadow/YYYYMMDD_HHMMSS.json。

用法：
    python tools/shadow_check.py --once
    python tools/shadow_check.py --loop --interval 300
    python tools/shadow_check.py --once --strict --domains sector,kline

判定口径见 docs/shadow_plan.md。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import struct
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover
    print("需要 requests：pip install requests", file=sys.stderr)
    raise SystemExit(2)

# ---------------------------------------------------------------- 常量

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "panel_v2" / "shadow"

OLD_8800 = os.environ.get("PANEL_SHADOW_OLD_8800", "http://127.0.0.1:8800")
# 旧 sector 端点在 8002（板块工作台），不在 8800；8800 上这三个路径全部 404。
OLD_8002 = os.environ.get("PANEL_SHADOW_OLD_8002", "http://127.0.0.1:8002")
NEW_8810 = os.environ.get("PANEL_SHADOW_NEW_8810", "http://127.0.0.1:8810")

TOL = 1e-6
MAX_SAMPLES = 20
TIMEOUT = 30
RETRIES = 2
MIN_INTERVAL = 30

KLINE_FIELDS = [
    ("o", "open"),
    ("h", "high"),
    ("l", "low"),
    ("c", "close"),
    ("v", "volume"),
    ("amount", "amount"),
]
# 旧端 bar 里新版没有的列，记录为未覆盖，不判 fail。
# v0.5.0（2026-10-23）：yellow/white 已实现（口径源 engine/common/indicators.py），
# 不再算「未覆盖」，纳入逐值比较。剩下的 amv/amvMa/amvWhite/chg/amp/turnover 仍未做。
KLINE_UNCOVERED_BAR_COLS = ["amv", "amvMa", "amvWhite", "chg", "amp", "turnover"]


# ---------------------------------------------------------------- 用例定义

@dataclass
class Case:
    domain: str
    name: str
    old_base: str
    old_path: str
    new_path: str
    old_params: dict[str, Any] | None = None
    new_params: dict[str, Any] | None = None
    comparator: str = "exact"  # exact | kline
    # 数据版本键（点路径）。这些路径不参与内容比对，只用于判断是否同一份数据。
    version_fields: tuple[str, ...] = ()
    # kline 专用：价格舍入容差（元）。默认 0.0101（1 分钱）；源精度只有 2 位小数的
    # 指数要单独放一点（399001.SZ 实测最大 0.03），否则会当成内容差异误报。
    price_tol: float = 0.0101
    # 已知旧端缺陷路径（正则）：命中则不判 fail，只记入 known_old_defects。
    # 用于「新版给出真值、旧端因数据源缺口给 None」这类已定案的差异。
    # ⚠ 配合 known_old_defect_codes 使用：路径正则只限定字段，**股票/板块范围靠另一道闸**。
    known_old_defect_patterns: tuple[str, ...] = ()
    # 已知缺陷只对这些代码生效（子串/后缀匹配，如 "920087"）。为空 = 不限代码（慎用）。
    # 存在的理由：审核实测发现 `rows[\d+]\.bull` 这类正则会把 600000 这类非北交票也算进去，
    # 于是「bull 从多变空」这种真回归也会被当成已知缺陷吞掉。
    known_old_defect_codes: tuple[str, ...] = ()
    # 额外自证条件：只有当旧值满足它才算「已知缺陷」（否则当普通差异）。
    # 支持 key: "n_total_over_rows" —— 旧 n_total 必须大于旧端实际返回行数
    # （即旧端自己报的数与行数矛盾，那才是已知缺陷）。
    known_old_defect_requires: tuple[str, ...] = ()
    # 并列名次容忍：`True` 时，`rows[i].sec_rank` / `rows[i].trail` 的差异若属于
    # 「同一日期上名次集合（多重集）相同」的互换，则不计差异。
    # 存在的理由：变化量恰好相等的股票本来就无先后之分（实测 885338 有 7 只 amv 恒 0
    # 的票变化量都是 0.0，旧端用 set 迭代序、新版用代码升序，名次在 1623-1629 间互换）。
    # 只跳过并列互换，非并列的任何位移照常判 fail。
    tie_aware_rank: bool = False
    # 允许新版缺失的旧端字段（正则）。**必须显式列**，否则旧端有、新版没有 = 判 fail。
    # 用途：声明「永久不搬」的旧字段（如 positions_list 需实时行情）。
    allowed_missing_old_keys: tuple[str, ...] = ()
    # 「日线源滞后」时可解释的字段（正则）：新版日线停在 T-1、旧端已有当日，
    # 这些字段（依赖当日收盘）必然新版 null。见 detect_source_lag()。
    lag_explainable: tuple[str, ...] = ()
    # 「旧端无值→新版有值」可放行的字段（正则）：只限定这一个方向。
    # 反向（旧有值、新版 null）**不放过** —— 那是新版退化。
    old_null_ok: tuple[str, ...] = ()
    # 盘中实时字段（叶子名）：与 live_tol_pct 配合，只差跳价时降为 live_skew，不判 fail。
    live_fields: tuple[str, ...] = ()
    live_tol_pct: float = 0.5


CASES: list[Case] = [
    # ---- sector（旧端 8002）
    Case("sector", "tree", OLD_8002, "/api/sector_tree", "/api/p/sector/tree"),
    Case(
        "sector", "rank", OLD_8002, "/api/sector_level", "/api/p/sector/rank",
        old_params={"level": "L2", "period": "1"},
        new_params={"level": "L2", "period": "1"},
        version_fields=("date",),
    ),
    Case(
        "sector", "series", OLD_8002, "/api/series", "/api/p/sector/series",
        old_params={"code": "881101.TI", "days": "60", "period": "1"},
        new_params={"code": "881101.TI", "days": "60", "period": "1"},
    ),
    # 全量成分股（含买点徽标）：period=1 时与旧端逐值相等；period=10/20 的差异已归因为
    # 旧端 920xxx vipdoc 路径缺陷（见 docs/sector_contract.md §6），不放进日常 shadow。
    Case(
        "sector", "members_full", OLD_8002, "/api/members", "/api/p/sector/members/881101.TI",
        old_params={"code": "881101.TI", "period": "1"},
        new_params={"full": "1", "period": "1", "limit": "0"},
        # 旧端 vipdoc 北交所缺一年（bj920087.day 20250930→20261009 断 374 天），
        # 920xxx 的 real_pct / amv_avg / bull 恒为 None；新版读 track_pool.db 有值。
        # 代码范围限定 920xxx：否则 600000 这类非北交票的 bull 变化也会被当成已知缺陷吞掉。
        known_old_defect_patterns=(r"^rows\[\d+\]\.(bull|real_pct|amv_pct|d_contrib|amt_avg)$",),
        known_old_defect_codes=("920",),
        # 16:40–18:00 空档：新版日线停在 T-1、旧端 vipdoc 已有当日收盘 →
        # real_pct / diverge 新版必为 null。用 detect_source_lag() 的证据解释，不判 fail。
        lag_explainable=(r"real_pct", r"diverge"),
        # 收盘后 `main_flow`：旧端全 null、新版有真值（旧端当日资金流未落盘）。
        # `old_null_ok` 只允许「旧端无值 → 新版有值」这个方向；
        # **反向不放过**（新版 null、旧有值 = 新版退化，照判 fail）。
        old_null_ok=(r"main_flow",),
        # 变化量恰好相等的股票本来就无先后（885338 有 7 只 amv 恒 0 的票名次在 1623-1629 互换），
        # 按名次多重集相同放过，非并列位移照常判 fail。
        tie_aware_rank=True,
        # **有意不搬**（不把旧面板的负担原样搬到新架构）：
        #   sector / code / name → 行身份字段，新版统一叫 level / stock_code / stock_name。
        # score / mkt_rank / sec_rank / trail / sec_total 自 2026-10-23 已接入
        # （口径源 `工具/panel_scores.py`，走 ctx.lib），因此**不再列在这里**：
        # 再列就等于放弃校验这五个字段，与“漏了就判 fail”的初衷相反。
        allowed_missing_old_keys=(
            r"^sector$",
            r"^rows\[\d+\]\.(code|name)$",
        ),
    ),
    # INDEX 层（000001.SH，全A 前 60 只）：无北交票，bull 逐值相等；
    # 仅 n_total 不同 —— 旧端报 2230（与它自己返回的行数 2181 矛盾），新版取实际成分数。
    Case(
        "sector", "members_full_index", OLD_8002, "/api/members", "/api/p/sector/members/000001.SH",
        old_params={"code": "000001.SH", "period": "1"},
        new_params={"full": "1", "period": "1", "limit": "0"},
        # n_total：旧端报 2230（与它自己返回的 2181 行矛盾）。
        # `n_total_over_rows` = 只有旧值 > 旧端实际行数时才当已知缺陷；
        # 否则（比如真把 n_total 改错成 0）仍然判 fail。
        known_old_defect_patterns=(r"^n_total$",),
        known_old_defect_requires=("n_total_over_rows",),
        tie_aware_rank=True,
        lag_explainable=(r"real_pct", r"diverge"),
        old_null_ok=(r"main_flow",),
        allowed_missing_old_keys=(
            r"^sector$",
            r"^rows\[\d+\]\.(code|name)$",
        ),
    ),
    Case(
        "sector", "minute_rank", OLD_8002, "/api/minute_rank", "/api/p/sector/minute_rank",
        old_params={"level": "L2", "n": "20"},
        new_params={"level": "L2", "n": "20"},
    ),
    # 个股评分轨迹（2026-10-23 新增）：旧端 rows 是 {code: trail}，新版是 [{code, trail, ...}]，
    # 形状有意不同（新版多带 name/score/mkt_rank），故走专用比较器只比轨迹。
    Case(
        "sector", "score_trail", OLD_8002, "/api/score_trail", "/api/p/sector/score_trail",
        old_params={"code": "881101.TI", "period": "1"},
        new_params={"code": "881101.TI", "period": "1"},
        comparator="score_trail",
    ),
    Case(
        "sector", "minute_series", OLD_8002, "/api/minute_series", "/api/p/sector/minute_series",
        old_params={"code": "881101.TI", "level": "L2", "period": "1"},
        new_params={"code": "881101.TI", "level": "L2", "period": "1"},
    ),
    Case(
        "sector", "minute_dates", OLD_8002, "/api/minute_dates", "/api/p/sector/minute_dates",
    ),
    Case(
        "sector", "rank_top", OLD_8002, "/api/rank", "/api/p/sector/rank_top",
        old_params={"level": "L2", "n": "1"},
        new_params={"level": "L2", "n": "1"},
    ),
    Case(
        "sector", "trend", OLD_8002, "/api/trend", "/api/p/sector/trend",
        old_params={"code": "881101.TI", "days": "120"},
        new_params={"code": "881101.TI", "days": "120"},
    ),
    Case(
        "sector", "board_top", OLD_8002, "/api/board_top", "/api/p/sector/board_top",
        old_params={"sector": "881101.TI", "top": "8"},
        new_params={"sector": "881101.TI", "top": "8"},
    ),
    # 旧端额外返 bull（双轨多空，口径源=工具/amv_regime.py）；新版同样返（走口径库白名单）。
    Case(
        "sector", "board_flow", OLD_8002, "/api/board_flow", "/api/p/sector/board_flow",
        old_params={"period": "1", "level": "L2"},
        new_params={"period": "1", "level": "L2"},
    ),
    Case(
        "sector", "contrib", OLD_8002, "/api/contrib", "/api/p/sector/contrib",
        old_params={"code": "881101.TI", "period": "1"},
        new_params={"code": "881101.TI", "period": "1"},
    ),
    Case(
        "sector", "contrib_p5", OLD_8002, "/api/contrib", "/api/p/sector/contrib",
        old_params={"code": "881101.TI", "period": "5"},
        new_params={"code": "881101.TI", "period": "5"},
    ),
    # ---- amv（旧端 8800）
    Case(
        "amv", "live", OLD_8800, "/api/amv_live.json", "/api/p/amv/live",
        version_fields=("asof", "meta.generated_at"),
    ),
    Case(
        "amv", "regime", OLD_8800, "/api/amv_regime_latest.json", "/api/p/amv/regime",
        version_fields=("date", "generated_at"),
    ),
    Case(
        "amv", "position_cap", OLD_8800, "/api/position_cap_latest.json", "/api/p/amv/position_cap",
        version_fields=("trade_date", "computed_at"),
    ),
    Case("amv", "hcl", OLD_8002, "/api/hcl", "/api/p/amv/hcl"),
    # ---- battle_plan（2026-11-09：只读插件 `battle_plan` 已并入 `compat`（mount=root），
    #      `/api/p/battle_plan/*` 已不存在；新端路径改到 compat 根路径。旧端 8800 已退役
    #      → 这三条实际跑「单端自检」（只验 200 + JSON 可解析）。
    Case(
        "battle_plan", "plan", OLD_8800, "/api/battle_plan.json", "/api/battle_plan.json",
        version_fields=("plan_date", "meta.generated_at"),
    ),
    Case(
        "battle_plan", "alerts", OLD_8800, "/api/alert_todo.json", "/api/alert_todo.json",
        version_fields=("date", "updated_at"),
    ),
    Case(
        "battle_plan", "positions", OLD_8800, "/api/real_positions", "/api/real_positions",
        version_fields=("updated_at", "last_synced_at", "account.updated_at"),
        # **有意不搬**：positions_list 需实时行情源（现价/PnL/权重），新版不接实时。
        allowed_missing_old_keys=(r"^positions_list$",),
        # ⚠ 已知旧端缺陷（2026-10-23 收盘后实测）：旧端用 eltdx 快照价 × qty **自己重算**市值，
        # 对标准券（888880.SH「新标准券」，100 元/张）取到的快照价是 1.00，于是算出
        # 2170 × 1.00 = 2170，而 QMT 落盘的真实市值是 217000（差 100 倍）。
        # 新版直接读 `real_positions.json` 的 market_value —— **以新版为准**。
        # 实例：旧 2253.6（= 83.6 + 2170）/ 新 217083.6（= 83.6 + 217000）。
        known_old_defect_patterns=(r"^market_value_total$",),
        # 盘中跳价：`market_value_total` 由持仓现价推得，旧 8800 现取快照、新版读文件。
        # 只放这一个字段，相对差 >0.5% 仍判 fail。
        live_fields=("market_value_total",),
        live_tol_pct=0.5,
    ),
    # 自选池：旧 8800 的 /api/watchlist.json 会当场富化行情/多空/徽标，新版只读原文件，
    # 因此只比对条目本身的共同字段。
    Case("watchlist", "items", OLD_8800, "/api/watchlist.json", "/api/p/watchlist/items",
         new_params={"limit": "1000"},
         # **有意不搬**：旧端的 meta/mode/groups/stats/quote_asof 是它当场富化时拼上去的，
         # 新版把“条目”与“行情”拆成两个端点（/items 与 /quotes），不再混在一个文档里。
         allowed_missing_old_keys=(
             r"^meta$", r"^mode$", r"^groups$", r"^stats$", r"^quote_asof$", r"^updated_at$",
             r"^items\[\d+\]\.(quote|quote_source|regime|sector|div|flow|badges)$",
             # 板块条目才有：旧端当场算板块涨跌/层级；新版 /quotes 只给个股行情，
             # 板块涨跌请走 sector 插件的 /rank（避免两个地方各算一份板块口径）。
             r"^items\[\d+\]\.(level|pct|quote_date)$",
         )),
    # `/levels`（关注池关键位）：旧 8800 走 `/api/selection/defense` 内部的同一文件，无独立 GET；
    # 新版只读 `focus_levels.json`，无旧端可对比（新能力，不进 shadow）。
    # ---- kline（旧端 8800）
    Case(
        "kline", "bars", OLD_8800, "/api/kline/000001.SH", "/api/p/kline/bars/000001.SH",
        old_params={"period": "d", "limit": "120"},
        new_params={"limit": "120", "adjust": "qfq"},
        comparator="kline",
    ),
    # ---- kline 实时三项（v0.4.0，2026-10-23 新增）
    # 这三个用例用**自定义比较器**：比较器自己决定比哪些字段，
    # 不走 compare_exact 的递归键比对（否则旧端多出的 auction/session/ticks 会全部变成 missing_in_new）。
    #
    # 分时：按 HH:MM 对齐比 close/avg/volume/amount；新旧请求间隔内会多出 1-2 根，不算差异。
    Case(
        "kline", "intraday", OLD_8800, "/api/intraday/600519.SH",
        "/api/p/kline/intraday/600519.SH",
        comparator="intraday",
    ),
    # 五档：
    # ⚠ 已知旧端缺陷：`_Q5_AUX_TTL = 1800.0`，aux 缓存 30 分钟，盘中它的涨跌停与量比
    #   用的是半小时前那版的昨收/均量（实测旧 1440.66 基于 10-21 收盘，今日应为 1450.34）。
    #   新版 aux 缓存 60s，用当日昨收 → **以新版为准**。
    Case(
        "kline", "quote5", OLD_8800, "/api/quote5/600519.SH",
        "/api/p/kline/quote5/600519.SH",
        comparator="quote5",
        known_old_defect_patterns=(r"^limit_up$", r"^limit_down$", r"^stats\.vol_ratio$"),
    ),
    # 筹码：按价位对齐；共同价位的量必须相等。
    Case(
        "kline", "volume_profile", OLD_8800, "/api/volume_profile/600519.SH",
        "/api/p/kline/volume_profile/600519.SH",
        comparator="volume_profile",
    ),
    # 多日分时：与单日同比较器（按 HH:MM 对齐，但要多层 days[]）。
    Case(
        "kline", "intraday_multi", OLD_8800, "/api/intraday_multi/600519.SH?days=5",
        "/api/p/kline/intraday_multi/600519.SH?days=5",
        comparator="intraday_multi",
    ),
    # M4 验收要求「已 diff 的 7 个标的 OHLCVA 无新增差异」：其余 6 个也上 shadow。
    # 399001.SZ 的 12 处差异是**源精度**（旧端 3 位小数 11820.012，库内 2 位 11819.99），
    # 实测最大 0.03，单独放宽到 0.031 并在报告里计入 precision_price，不算 fail。
    #
    # 512480.SH 不在日常 shadow：它的复权因子在 `track_pool.db::factor` 里**全为 1.0**，
    # 而 2026-07-03 有一次份额折算（2.70 → 1.331）→ 旧端（tdx gbbq 前复权）给 0.839，
    # 新版返回原始价 1.677。属**数据缺口**（factor 表缺该事件），不是插件逻辑差异；
    # kline 插件已在 `warnings` 里显式报「疑似缺复权因子」。待 factor 补数后再入 shadow。
    *[
        Case(
            "kline", f"bars_{code}", OLD_8800, f"/api/kline/{code}", f"/api/p/kline/bars/{code}",
            old_params={"period": "d", "limit": "120"},
            new_params={"limit": "120", "adjust": "qfq"},
            comparator="kline",
            price_tol=0.031 if code == "399001.SZ" else 0.0101,
        )
        for code in ("000001.SZ", "600000.SH", "300750.SZ", "399006.SZ", "399001.SZ")
    ],
    # 个股四态背景带：旧端 `/api/kline_bg/{code}?mode=stock` 返 `items{date:state}` 全历史；
    # 新版只返 `dates[]+states[]`（含 60 根预热，已与旧端零差异）。结构不同 → 专用比较器。
    *[
        Case(
            "kline", f"bg_{code}", OLD_8800, f"/api/kline_bg/{code}", f"/api/p/kline/bg/{code}",
            old_params={"mode": "stock"},
            new_params={"limit": "60"},
            comparator="bg",
        )
        for code in ("600000.SH", "000001.SZ")
    ],
]


# ---------------------------------------------------------------- 工具函数

def _brief(v: Any, limit: int = 160) -> Any:
    """把值压成可落盘的短表示。"""
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    if isinstance(v, list):
        if len(v) <= 3 and all(isinstance(x, (str, int, float, bool)) or x is None for x in v):
            return v
        return f"<list[{len(v)}]>"
    if isinstance(v, dict):
        return f"<dict[{len(v)}]>"
    s = repr(v)
    return s[:limit]


def _f32(x: float) -> float:
    """模拟 float32 存储精度。"""
    try:
        return struct.unpack("f", struct.pack("f", float(x)))[0]
    except (OverflowError, struct.error):
        return float(x)


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _drop_tie_permutations(
    diffs: list[dict[str, Any]], old: dict, new: dict
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """丢弃「并列组内互换」造成的 sec_rank / trail 差异。

    判定法：把两侧响应里每只股票的 trail 展平为 `(日期 -> 名次)`，再按日期聚合
    成**名次多重集**。两个多重集相等 ⇒ 同一批股票只是内部顺序不同，不存在真实位移。
    只要有一个日期对不上，就一条也不放过（宁可多报）。

    局限（故意）：只看名次集合，不看它属于哪只股票。具体到「A 与 B 互换了」无法区分
    ——但那正是并列的定义，无先后可言。
    """
    def _rank_map(payload: dict) -> dict[str, list[int]]:
        out: dict[str, list[int]] = {}
        rows = payload.get("rows")
        if not isinstance(rows, list):
            return out
        for r in rows:
            if not isinstance(r, dict):
                continue
            trail = r.get("trail")
            if isinstance(trail, list):
                for item in trail:
                    if isinstance(item, (list, tuple)) and len(item) >= 2:
                        d, k = item[0], item[1]
                        if d is not None and isinstance(k, int):
                            out.setdefault(str(d), []).append(k)
            sr = r.get("sec_rank")
            if isinstance(sr, int):
                # sec_rank 已包含在当前日 trail 里；无 trail 的旧端才需要单列
                pass
        return out

    o, n = _rank_map(old), _rank_map(new)
    if not o or not n:
        return diffs, []
    for d in set(o) | set(n):
        if sorted(o.get(d) or []) != sorted(n.get(d) or []):
            return diffs, []          # 有任何一天不一致 → 不启用容忍

    keep: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for d in diffs:
        path = str(d.get("path") or "")
        if re.search(r"\.(sec_rank|trail)$", path):
            skipped.append(d)
        else:
            keep.append(d)
    return keep, skipped


def _row_codes(payload: Any) -> dict[int, str]:
    """`{行索引: 股票代码}`（从旧端响应的 `rows[i].code` 取）。

    用于把 diffs 里的 `rows[16].bull` 反向映射到具体股票，
    从而让「已知旧端缺陷」只限在真正受害的那批代码上。
    """
    out: dict[int, str] = {}
    if not isinstance(payload, dict):
        return out
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return out
    for i, r in enumerate(rows):
        if isinstance(r, dict):
            code = r.get("code") or r.get("stock_code")
            if code:
                out[i] = str(code)
    return out


def _num_equal(old: Any, new: Any) -> tuple[bool, bool]:
    """返回 (是否在容差内相等, 是否由 float32 精度解释)。"""
    if abs(float(old) - float(new)) <= TOL:
        return True, False
    # 整数量级的大数（成交量/成交额）在旧端可能经过 float32 落盘。
    if _is_num(old) and _is_num(new):
        if _f32(new) == float(old) or _f32(old) == float(new):
            return False, True
    return False, False


# ---------------------------------------------------------------- 比较器

def compare_exact(case: Case, old: Any, new: Any) -> dict[str, Any]:
    """递归比对两端的共同原始键。

    - **旧端有、新版没有 → 判 fail**（`missing_in_new`）；只有 `case.allowed_missing_old_keys`
      里显式声明的字段（正则）才记入 `ignored_old_keys` 不判 fail。
      （2026-10-23 审核修正：此前所有缺失都记 ignored_old_keys，漏字段永远抓不到。）
    - 新端独有的键记 `extra_new_keys`，不判 fail。
    - `version_fields` 指定的路径不参与内容比对。
    """
    skip = set(case.version_fields)
    allow_missing = [re.compile(p) for p in case.allowed_missing_old_keys]
    out: dict[str, Any] = {
        "diffs": [],
        "version_drift": [],
        "ignored_old_keys": [],
        "missing_in_new": [],
        "extra_new_keys": [],
        "uncovered_old_fields": [],
        "precision_fp32": 0,
    }

    def drift(path: str, o: Any, n: Any) -> None:
        """版本键只在两端取值不同时记 drift —— 相同值不是漂移。"""
        if o != n:
            out["version_drift"].append({"path": path, "old": _brief(o), "new": _brief(n)})

    def walk(o: Any, n: Any, path: str) -> None:
        if path in skip:
            drift(path, o, n)
            return

        if isinstance(o, dict) and isinstance(n, dict):
            for k in o:
                sub = f"{path}.{k}" if path else k
                if k not in n:
                    if sub in skip:
                        out["version_drift"].append({"path": sub, "old": _brief(o[k]), "new": "<MISSING>"})
                        continue
                    if any(p.match(sub) for p in allow_missing):
                        out["ignored_old_keys"].append(sub)
                    else:
                        # 旧端有、新版没有，且未声明允许缺失 → 真缺失，判 fail
                        out["missing_in_new"].append(sub)
                    continue
                walk(o[k], n[k], sub)
            for k in n:
                if k not in o:
                    sub = f"{path}.{k}" if path else k
                    if sub in skip:
                        continue
                    out["extra_new_keys"].append(sub)
            return

        if isinstance(o, list) and isinstance(n, list):
            if len(o) != len(n):
                out["diffs"].append({"path": path, "kind": "len", "old": len(o), "new": len(n)})
            for i in range(min(len(o), len(n))):
                walk(o[i], n[i], f"{path}[{i}]")
            return

        if o is None and n is None:
            return
        if isinstance(o, bool) or isinstance(n, bool):
            if o is not n:
                out["diffs"].append({"path": path, "kind": "value", "old": o, "new": n})
            return
        if _is_num(o) and _is_num(n):
            ok, fp32 = _num_equal(o, n)
            if not ok:
                if fp32:
                    out["precision_fp32"] += 1
                else:
                    out["diffs"].append({"path": path, "kind": "value", "old": o, "new": n})
            return
        if o != n:
            out["diffs"].append({"path": path, "kind": "value", "old": _brief(o), "new": _brief(n)})

    walk(old, new, "")
    return out


def compare_kline(case: Case, old: Any, new: Any) -> dict[str, Any]:
    """只比共同日期、OHLCV/amount；旧端多出的指标列记录为未覆盖。"""
    out: dict[str, Any] = {
        "diffs": [],
        "version_drift": [],
        "ignored_old_keys": [],
        "extra_new_keys": [],
        "uncovered_old_fields": [],
        "precision_fp32": 0,
        "precision_price": 0,
        "bars": {},
    }
    if not isinstance(old, dict) or not isinstance(new, dict):
        out["diffs"].append({"path": "", "kind": "shape", "old": type(old).__name__, "new": type(new).__name__})
        return out

    old_bars = old.get("bars") or []
    new_bars = new.get("bars") or []
    ob = {b.get("t"): b for b in old_bars if isinstance(b, dict)}
    nb = {b.get("date"): b for b in new_bars if isinstance(b, dict)}
    common = sorted(set(ob) & set(nb))

    out["bars"] = {
        "old": len(ob),
        "new": len(nb),
        "common": len(common),
        "only_old": sorted(set(ob) - set(nb)),
        "only_new": sorted(set(nb) - set(ob)),
    }
    # 归类窗口错位：旧端带当日未收盘 bar（v0.1 契约不含），新版窗口整体前移。
    # 这类不共同日期不算 diff，但必须显式记录，不能默默吞掉。
    old_dates = sorted(ob)
    new_dates = sorted(nb)
    win: dict[str, Any] = {}
    if old_dates and new_dates:
        tail = [d for d in sorted(set(ob) - set(nb)) if d > new_dates[-1]]
        head = [d for d in sorted(set(nb) - set(ob)) if d < old_dates[0]]
        if tail:
            win["old_tail_intraday"] = tail  # 旧端多出的当日/未收盘 bar
        if head:
            win["new_head_backfill"] = head  # 新端窗口前移补位
        win["old_range"] = [old_dates[0], old_dates[-1]]
        win["new_range"] = [new_dates[0], new_dates[-1]]
    if win:
        out["bars"]["window"] = win

    # 黄白线（v0.5.0）：值依赖**窗口内收盘序列**（EMA 种子=首值，黄线还需 114 根），
    # 所以窗口一定错位时前部天然不同（旧端多带一根当日 bar → 起点差一天）。
    # 口径：
    # • 窗口完全对齐 → 全窗口逐值比
    # • 有错位 → **只比尾部 5 根**（EMA 已收敛），其余差异不判 fail 但总数可见
    tail_n = 5
    yw_diffs: list[dict[str, Any]] = []
    yw_checked = 0
    if common:
        aligned = (old_dates == new_dates)
        check_dates = common if aligned else common[-tail_n:]
        for d in check_dates:
            for k in ("yellow", "white"):
                ov, nv = ob[d].get(k), nb[d].get(k)
                if ov is None and nv is None:
                    continue
                yw_checked += 1
                if ov is None or nv is None:
                    yw_diffs.append({"path": f"bars[{d}].{k}", "kind": "value",
                                     "old": _brief(ov), "new": _brief(nv)})
                    continue
                if not (_is_num(ov) and _is_num(nv)):
                    yw_diffs.append({"path": f"bars[{d}].{k}", "kind": "value",
                                     "old": _brief(ov), "new": _brief(nv)})
                    continue
                if abs(float(ov) - float(nv)) > case.price_tol:
                    yw_diffs.append({"path": f"bars[{d}].{k}", "kind": "value",
                                     "old": _brief(ov), "new": _brief(nv)})
    out["diffs"].extend(yw_diffs)
    out["bars"]["yellow_white"] = {"checked": yw_checked, "diffs": len(yw_diffs),
                                  "scope": "aligned" if (common and old_dates == new_dates) else f"tail{tail_n}"}

    for d in common:
        for ok, nk in KLINE_FIELDS:
            if ok not in ob[d] or nk not in nb[d]:
                out["diffs"].append({"path": f"bars[{d}].{nk}", "kind": "missing", "old": ok in ob[d], "new": nk in nb[d]})
                continue
            ov, nv = ob[d][ok], nb[d][nk]
            if not (_is_num(ov) and _is_num(nv)):
                if ov != nv:
                    out["diffs"].append({"path": f"bars[{d}].{nk}", "kind": "value", "old": _brief(ov), "new": _brief(nv)})
                continue
            ok_eq, fp32 = _num_equal(ov, nv)
            if not ok_eq:
                # 价格 1 分钱：旧端实时源与新库前复权各自四舍五入到分，属精度边界。
                if nk in ("open", "high", "low", "close") and abs(float(ov) - float(nv)) <= case.price_tol:
                    out["precision_price"] += 1
                elif fp32:
                    out["precision_fp32"] += 1
                else:
                    out["diffs"].append({"path": f"bars[{d}].{nk}", "kind": "value", "old": ov, "new": nv})

    # 空交集不得静默 pass：日期键格式一变（或窗口完全错开）就会 common=0，
    # 那意味着“根本没比”，不是“比过一样”。
    if not common:
        out["diffs"].append({"path": "bars", "kind": "empty_intersection",
                              "old": len(ob), "new": len(nb)})

    # 顶层契约差异只记录，不判 fail。
    out["ignored_old_keys"] = [k for k in old if k not in ("bars",) and k not in new]
    out["extra_new_keys"] = [k for k in new if k not in ("bars",) and k not in old]
    if old_bars:
        cols = [k for k in old_bars[0] if k not in ("t", "o", "h", "l", "c", "v", "amount")]
        out["uncovered_old_fields"] = [c for c in cols if c in KLINE_UNCOVERED_BAR_COLS] or cols
    return out


def compare_bg(case: Case, old: Any, new: Any) -> dict[str, Any]:
    """个股四态背景带：旧端 `items{date:state}` vs 新版 `dates[]+states[]`。

    只比**共同日期**的状态；旧端多出的历史日期不判 fail（新版只返窗口内）。
    """
    out: dict[str, Any] = {
        "diffs": [], "version_drift": [], "ignored_old_keys": [],
        "extra_new_keys": [], "uncovered_old_fields": [], "precision_fp32": 0,
    }
    if not isinstance(old, dict) or not isinstance(new, dict):
        out["diffs"].append({"path": "", "kind": "shape",
                             "old": type(old).__name__, "new": type(new).__name__})
        return out
    oi = old.get("items") or {}
    nd = new.get("dates") or []
    ns = new.get("states") or []
    if len(nd) != len(ns):
        out["diffs"].append({"path": "states", "kind": "len", "old": len(nd), "new": len(ns)})
        return out
    nm = dict(zip(nd, ns))
    common = [d for d in nd if d in oi]
    for d in common:
        if oi[d] != nm[d]:
            out["diffs"].append({"path": f"states[{d}]", "kind": "value",
                                 "old": oi[d], "new": nm[d]})
    # 空交集不得静默 pass（同 compare_kline）
    if not common:
        out["diffs"].append({"path": "states", "kind": "empty_intersection",
                              "old": len(oi), "new": len(nd)})
    out["ignored_old_keys"] = [k for k in old if k not in new]
    out["extra_new_keys"] = [k for k in new if k not in old]
    out["bars"] = {"old": len(oi), "new": len(nd), "common": len(common)}
    return out


def compare_score_trail(case: Case, old: Any, new: Any) -> dict[str, Any]:
    """个股评分轨迹：旧端 `rows{code: [[date, score], ...]}` vs 新版 `rows[{code, trail, ...}]`。

    形状不同是**有意的**（新版顺带带上 name / score / mkt_rank），所以不能走 exact。
    这里只比轨迹本身，逐值相等；某只股票在一侧没轨迹而另一侧有 → 计差异。
    """
    out: dict[str, Any] = {
        "diffs": [], "version_drift": [], "ignored_old_keys": [],
        "extra_new_keys": [], "uncovered_old_fields": [], "precision_fp32": 0,
    }
    o = (old.get("rows") if isinstance(old, dict) else None) or {}
    nrows = (new.get("rows") if isinstance(new, dict) else None) or []
    n = {r.get("code"): (r.get("trail") or []) for r in nrows if isinstance(r, dict)}
    common = set(o) & set(n)
    if not common:
        out["diffs"].append({"path": "rows", "kind": "empty_intersection",
                              "old": len(o), "new": len(n)})
        return out
    for c in sorted(set(o) - set(n)):
        out["diffs"].append({"path": "rows.%s" % c, "kind": "missing_in_new", "old": "present"})
    bad = 0
    for c in sorted(common):
        a, b = o[c] or [], n[c] or []
        if a != b:
            bad += 1
            if bad <= 20:
                out["diffs"].append({"path": "rows.%s.trail" % c, "kind": "value",
                                     "old": a[:2], "new": b[:2]})
    if bad > 20:
        out["diffs"].append({"path": "rows.*.trail", "kind": "truncated", "count": bad})
    out["bars"] = {"old": len(o), "new": len(n), "common": len(common)}
    return out


def compare_intraday(case: Case, old: Any, new: Any) -> dict[str, Any]:
    """分时：旧端 `points[{t,close,avg,volume,amount,cum_volume}]` vs 新版同名字段。

    按 **`t`（HH:MM）** 对齐，只比共同时刻。新版可能多出旧端请求之后才产生的分钟，
    旧端也可能因请求更早而少几根——都不判 fail。
    价格容差 1 分钱（`price_tol`），成交量/额按整数比。
    """
    out: dict[str, Any] = {
        "diffs": [], "version_drift": [], "ignored_old_keys": [],
        "extra_new_keys": [], "uncovered_old_fields": [], "precision_fp32": 0,
        "precision_price": 0,
    }
    if not isinstance(old, dict) or not isinstance(new, dict):
        out["diffs"].append({"path": "", "kind": "shape",
                             "old": type(old).__name__, "new": type(new).__name__})
        return out
    op = old.get("points") or []
    np_ = new.get("points") or []
    om = {str(p.get("t")): p for p in op if isinstance(p, dict)}
    nm = {str(p.get("t")): p for p in np_ if isinstance(p, dict)}
    common = [t for t in nm if t in om]
    # **正在形成的那根**：两次请求间隔内它还会变（实测 13:14 那根 19100 vs 2200 手）。
    # 注意两端末根可能不同（old 末根 13:17、new 末根 13:18），
    # 但只要某一端把它当末根，它就不是最终值 → **两端末根都要排除**。
    live_tail: list[str] = []
    if op:
        live_tail.append(str(op[-1].get("t")))
    if np_:
        t_new = str(np_[-1].get("t"))
        if t_new not in live_tail:
            live_tail.append(t_new)
    excluded = [t for t in common if t in live_tail]
    common = [t for t in common if t not in live_tail]
    for t in common:
        a, b = om[t], nm[t]
        for key in ("close", "avg"):
            ov, nv = a.get(key), b.get(key)
            if ov is None or nv is None:
                if ov != nv:
                    out["diffs"].append({"path": f"points[{t}].{key}", "kind": "value",
                                         "old": ov, "new": nv})
                continue
            if abs(float(ov) - float(nv)) <= case.price_tol:
                if float(ov) != float(nv):
                    out["precision_price"] += 1
                continue
            out["diffs"].append({"path": f"points[{t}].{key}", "kind": "value",
                                 "old": ov, "new": nv})
        for key in ("volume", "amount", "cum_volume"):
            ov, nv = a.get(key), b.get(key)
            if ov is None and nv is None:
                continue
            if ov is None or nv is None:
                out["diffs"].append({"path": f"points[{t}].{key}", "kind": "value",
                                     "old": ov, "new": nv})
                continue
            if abs(float(ov) - float(nv)) > 0.5:  # 成交量/额：半单位容差
                out["diffs"].append({"path": f"points[{t}].{key}", "kind": "value",
                                     "old": ov, "new": nv})
    if not common:
        out["diffs"].append({"path": "points", "kind": "empty_intersection",
                              "old": len(op), "new": len(np_)})
    out["live_tail"] = excluded
    # 昨收：两端都该有且相等
    opc, npc = old.get("prev_close"), new.get("pre_close")
    if opc is not None and npc is not None and abs(float(opc) - float(npc)) > 0.011:
        out["diffs"].append({"path": "pre_close", "kind": "value", "old": opc, "new": npc})
    out["bars"] = {"old": len(op), "new": len(np_), "common": len(common)}
    return out


def compare_quote5(case: Case, old: Any, new: Any) -> dict[str, Any]:
    """五档盘口：**比结构，不比瞬时数值**。

    盘口是高频数据：两次 HTTP 相隔几百毫秒，last 就可能差一两个 tick
    （实测 13:16 旧 1333.00 / 新 1332.74）。逐值比只会天天报假差异，
    所以这里改成校验「盘口自洽性 + 两端一致性在合理范围」：

    - 价格类字段：相对差 ≤ `QUOTE5_LIVE_TOL`（0.5%）→ 记 `snapshot_drift`，不算 diff
    - 五档：两端档数一致；各自价格单调（卖升序、买降序）；买1 ≤ last ≤ 卖1
    - 档位量（vol）：**不比**——一次吃货就能整档归零，比了纯粹是噪声
    - stats.total_vol / outer / inner：只验证非负与量级（新旧差异 ≤5%）
    - `turnover` / `vol_ratio`：旧端 `_Q5_AUX_TTL=1800s` 缓存 30 分钟，必然过期，由用例声明为已知缺陷

    这样能抓到真缺陷（档位错乱、方向反了、字段全 null），不会被行情跳动干扰。
    """
    out: dict[str, Any] = {
        "diffs": [], "version_drift": [], "ignored_old_keys": [],
        "extra_new_keys": [], "uncovered_old_fields": [], "precision_fp32": 0,
        "precision_price": 0,
    }
    if not isinstance(old, dict) or not isinstance(new, dict):
        out["diffs"].append({"path": "", "kind": "shape",
                             "old": type(old).__name__, "new": type(new).__name__})
        return out
    drift: list[str] = []
    for key in ("last", "pre_close", "change", "change_pct", "high", "low",
                "limit_up", "limit_down"):
        ov, nv = old.get(key), new.get(key)
        if ov is None and nv is None:
            continue
        if ov is None or nv is None:
            out["diffs"].append({"path": key, "kind": "value", "old": ov, "new": nv})
            continue
        base = max(abs(float(ov)), abs(float(nv)), 1e-9)
        rel = abs(float(ov) - float(nv)) / base
        if rel > 0.005:  # 0.5%
            out["diffs"].append({"path": key, "kind": "value", "old": ov, "new": nv})
        elif float(ov) != float(nv):
            drift.append(key)

    # 五档结构校验
    for side in ("bids", "asks"):
        ol = old.get(side) or []
        nl = new.get(side) or []
        if len(ol) != len(nl):
            out["diffs"].append({"path": side, "kind": "len",
                                 "old": len(ol), "new": len(nl)})
            continue
        prices = [float(x.get("price", 0)) for x in nl]
        if side == "asks" and prices != sorted(prices):
            out["diffs"].append({"path": f"{side}.order", "kind": "not_monotonic",
                                 "old": "—", "new": prices})
        if side == "bids" and prices != sorted(prices, reverse=True):
            out["diffs"].append({"path": f"{side}.order", "kind": "not_monotonic",
                                 "old": "—", "new": prices})
        # 两端对应档位的价格不应相差太远（吃单会移动，但不会跳几个百分点）
        for i, (a, b) in enumerate(zip(ol, nl)):
            pa, pb = a.get("price"), b.get("price")
            if pa is None or pb is None:
                continue
            base = max(abs(float(pa)), abs(float(pb)), 1e-9)
            if abs(float(pa) - float(pb)) / base > 0.02:  # 2%
                out["diffs"].append({"path": f"{side}[{i}].price", "kind": "value",
                                     "old": pa, "new": pb})

    # 盘口自洽：买1 ≤ last ≤ 卖1（允许 last 缺失）
    lt = new.get("last")
    if lt is not None:
        b1 = (new.get("bids") or [{}])[0].get("price")
        a1 = (new.get("asks") or [{}])[0].get("price")
        if b1 is not None and a1 is not None:
            if not (float(b1) <= float(lt) <= float(a1) * 1.001):
                out["diffs"].append({"path": "book.crossed", "kind": "inconsistent",
                                     "old": f"bid1={b1} ask1={a1}", "new": f"last={lt}"})

    # 成交量类：只验证量级（两次快照差 ≤5%）
    os_, ns_ = old.get("stats") or {}, new.get("stats") or {}
    for key in ("now_vol", "total_vol", "outer", "inner"):
        ov, nv = os_.get(key), ns_.get(key)
        if ov is None and nv is None:
            continue
        if ov is None or nv is None:
            out["diffs"].append({"path": f"stats.{key}", "kind": "value", "old": ov, "new": nv})
            continue
        base = max(abs(float(ov)), abs(float(nv)), 1)
        if abs(float(ov) - float(nv)) / base > 0.05:
            out["diffs"].append({"path": f"stats.{key}", "kind": "value", "old": ov, "new": nv})
    # turnover / vol_ratio：旧端 aux 缓存 30 分钟，交给 known_old_defect_patterns
    for key in ("turnover", "vol_ratio"):
        ov, nv = os_.get(key), ns_.get(key)
        if ov is None and nv is None:
            continue
        if ov is None or nv is None:
            out["diffs"].append({"path": f"stats.{key}", "kind": "value", "old": ov, "new": nv})
            continue
        base = max(abs(float(ov)), abs(float(nv)), 1e-9)
        if abs(float(ov) - float(nv)) / base > 0.10:  # 10%（aux 过期的影响上界）
            out["diffs"].append({"path": f"stats.{key}", "kind": "value", "old": ov, "new": nv})

    out["snapshot_drift"] = drift
    out["ignored_old_keys"] = [k for k in old if k in ("ticks", "asof", "quote_time", "source")]
    return out


def compare_volume_profile(case: Case, old: Any, new: Any) -> dict[str, Any]:
    """筹码分布：按**价位**对齐，逐价比较量。

    价位是浮点，用 round(p, 3) 作键（eltdx 价精度到 0.001）。
    新版多出/少有的价位不判 fail（两次请求间隔内新成交会产生新价位）；
    但共同价位的量必须相等，且 `max_v` / `total_v` 不能差太多。
    """
    out: dict[str, Any] = {
        "diffs": [], "version_drift": [], "ignored_old_keys": [],
        "extra_new_keys": [], "uncovered_old_fields": [], "precision_fp32": 0,
    }
    if not isinstance(old, dict) or not isinstance(new, dict):
        out["diffs"].append({"path": "", "kind": "shape",
                             "old": type(old).__name__, "new": type(new).__name__})
        return out
    ob = {round(float(b.get("p", 0)), 3): int(b.get("v", 0)) for b in (old.get("buckets") or [])}
    nb = {round(float(b.get("p", 0)), 3): int(b.get("v", 0)) for b in (new.get("buckets") or [])}
    common = set(ob) & set(nb)
    # 盘中两份快照必然差几笔成交（两次 HTTP 间隔 50-300ms），所以逐值相等是**不可能**的。
    # 判定口径：
    # • 每个价位的量只允许**后期一侧不少于前期一侧**（时间只会增不会减）；
    # • 差异的**绝对值总和** 占总成交量 ≤ 1% → 视为快照差，不判 fail，记 `snapshot_drift`。
    # 超过 1% 就是真差异（口径错、剔错了竞价、或漏页）。
    drift = 0
    for p in sorted(common):
        if ob[p] == nb[p]:
            continue
        drift += abs(ob[p] - nb[p])
        if ob[p] != nb[p] and min(ob[p], nb[p]) > 0 and max(ob[p], nb[p]) / min(ob[p], nb[p]) > 3:
            # 同一价位量差 3 倍以上：不是几笔成交能解释的
            out["diffs"].append({"path": f"buckets[{p}]", "kind": "value",
                                 "old": ob[p], "new": nb[p]})
    if not common:
        out["diffs"].append({"path": "buckets", "kind": "empty_intersection",
                              "old": len(ob), "new": len(nb)})
    ot, nt = old.get("total_v"), new.get("total_v")
    base = max(abs(ot or 0), abs(nt or 0), 1)
    if isinstance(ot, (int, float)) and isinstance(nt, (int, float)):
        if drift / base > 0.01:
            out["diffs"].append({"path": "buckets.drift", "kind": "value",
                                 "old": drift, "new": base, "note": "逐值差异总和 > 总量1%"})
        elif drift:
            out["snapshot_drift"] = drift  # 可见但不判 fail
    out["bars"] = {"old": len(ob), "new": len(nb), "common": len(common), "drift": drift}
    return out


def compare_intraday_multi(case: Case, old: Any, new: Any) -> dict[str, Any]:
    """多日分时：两端都是 `days[{date, prev_close, points[]}]`，逐日逐点比。

    只比共同日期；每日内再按 `t` 对齐。昨天之前的历史日数据不会变，
    因此这几日应当零差异；当日可能因请求间隔多出几根，不算差异。
    """
    out: dict[str, Any] = {
        "diffs": [], "version_drift": [], "ignored_old_keys": [],
        "extra_new_keys": [], "uncovered_old_fields": [], "precision_fp32": 0,
        "precision_price": 0,
    }
    if not isinstance(old, dict) or not isinstance(new, dict):
        out["diffs"].append({"path": "", "kind": "shape",
                             "old": type(old).__name__, "new": type(new).__name__})
        return out
    od = {str(d.get("date")): d for d in (old.get("days") or []) if isinstance(d, dict)}
    nd = {str(d.get("date")): d for d in (new.get("days") or []) if isinstance(d, dict)}
    common_days = [d for d in nd if d in od]
    total_points = 0
    for day in sorted(common_days):
        o, n = od[day], nd[day]
        opc, npc = o.get("prev_close"), n.get("prev_close")
        if opc is not None and npc is not None and abs(float(opc) - float(npc)) > 0.011:
            out["diffs"].append({"path": f"days[{day}].prev_close", "kind": "value",
                                 "old": opc, "new": npc})
        om = {str(p.get("t")): p for p in (o.get("points") or []) if isinstance(p, dict)}
        nm = {str(p.get("t")): p for p in (n.get("points") or []) if isinstance(p, dict)}
        live_tail = set()
        if (o.get("points") or []):
            live_tail.add(str((o.get("points") or [])[-1].get("t")))
        if (n.get("points") or []):
            live_tail.add(str((n.get("points") or [])[-1].get("t")))
        for t in (x for x in nm if x in om):
            total_points += 1
            # 最后一天的两端末根都是“正在形成”，不判差异；历史日不跳过。
            if day == sorted(common_days)[-1] and t in live_tail:
                continue
            a, b = om[t], nm[t]
            for key in ("close", "avg"):
                ov, nv = a.get(key), b.get(key)
                if ov is None and nv is None:
                    continue
                if ov is None or nv is None:
                    out["diffs"].append({"path": f"days[{day}].{t}.{key}", "kind": "value",
                                         "old": ov, "new": nv})
                    continue
                if abs(float(ov) - float(nv)) > case.price_tol:
                    out["diffs"].append({"path": f"days[{day}].{t}.{key}", "kind": "value",
                                         "old": ov, "new": nv})
                elif float(ov) != float(nv):
                    out["precision_price"] += 1
            for key in ("volume", "amount", "cum_volume"):
                ov, nv = a.get(key), b.get(key)
                if ov is None and nv is None:
                    continue
                if ov is None or nv is None:
                    out["diffs"].append({"path": f"days[{day}].{t}.{key}", "kind": "value",
                                         "old": ov, "new": nv})
                    continue
                if abs(float(ov) - float(nv)) > 0.5:
                    out["diffs"].append({"path": f"days[{day}].{t}.{key}", "kind": "value",
                                         "old": ov, "new": nv})
    if not common_days:
        out["diffs"].append({"path": "days", "kind": "empty_intersection",
                              "old": len(od), "new": len(nd)})
    out["bars"] = {"old": len(od), "new": len(nd), "common": len(common_days),
                   "points": total_points}
    return out


COMPARATORS = {"exact": compare_exact, "kline": compare_kline, "bg": compare_bg,
               "score_trail": compare_score_trail,
               "intraday": compare_intraday, "intraday_multi": compare_intraday_multi,
               "quote5": compare_quote5,
               "volume_profile": compare_volume_profile}


# ---------------------------------------------------------------- HTTP

def _is_conn_refused(err: Any) -> bool:
    """旧端「拒绝连接」判定（2026-11-09：退役端口 = 拒绝连接，不是超时/500）。

    requests 连接被拒时抛 `ConnectionError(MaxRetryError(NewConnectionError(...)))`，
    中文环境会说「由于目标计算机积极拒绝」；两者都认。
    """
    s = str(err or "")
    if not s:
        return False
    marks = ("ConnectionRefusedError", "NewConnectionError", "Max retries exceeded",
             "积极拒绝", "拒绝连接", "Connection refused")
    return any(m in s for m in marks)


def fetch(session: requests.Session, base: str, path: str, params: dict | None) -> dict[str, Any]:
    url = base.rstrip("/") + path
    t0 = time.perf_counter()
    last_err = None
    for attempt in range(RETRIES + 1):
        try:
            r = session.get(url, params=params, timeout=TIMEOUT)
            ms = round((time.perf_counter() - t0) * 1000, 1)
            if r.status_code >= 500:
                last_err = f"HTTP {r.status_code}"
                if attempt < RETRIES:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                return {"ok": False, "status": r.status_code, "ms": ms, "url": r.url, "error": last_err}
            if r.status_code >= 400:
                return {"ok": False, "status": r.status_code, "ms": ms, "url": r.url, "error": f"HTTP {r.status_code}", "body": r.text[:300]}
            try:
                body = r.json()
            except ValueError as e:
                return {"ok": False, "status": r.status_code, "ms": ms, "url": r.url, "error": f"JSONDecodeError: {e}", "body": r.text[:300]}
            return {"ok": True, "status": r.status_code, "ms": ms, "url": r.url, "data": body}
        except requests.RequestException as e:
            last_err = f"{type(e).__name__}: {e}"
            if attempt < RETRIES:
                time.sleep(0.5 * (attempt + 1))
                continue
            ms = round((time.perf_counter() - t0) * 1000, 1)
            return {"ok": False, "status": None, "ms": ms, "url": url, "error": last_err}
    ms = round((time.perf_counter() - t0) * 1000, 1)
    return {"ok": False, "status": None, "ms": ms, "url": url, "error": last_err}


# ---------------------------------------------------------------- 单轮执行

def _live_skew_only(diffs: list[dict[str, Any]], live_fields: tuple[str, ...],
                    tol_pct: float) -> bool:
    """所有差异是否都属于「盘中实时字段 + 相对差 ≤ tol_pct」。

    有一个不满足就 False——宁可误判 fail，也不能把真差异当跳价放过。
    存在的理由（2026-10-23 10:07 实测）：`market_value_total` 旧 78.3 / 新 78.4，
    旧 8800 取 eltdx 实时快照、新版读 `real_positions.json`，两份盘口差一个跳价。
    """
    if not diffs or not live_fields:
        return False
    for d in diffs:
        leaf = str(d.get("path", "")).rsplit(".", 1)[-1]
        if leaf not in live_fields:
            return False
        ov, nv = d.get("old"), d.get("new")
        if not (isinstance(ov, (int, float)) and isinstance(nv, (int, float))):
            return False
        base = max(abs(float(ov)), abs(float(nv)), 1e-9)
        if abs(float(ov) - float(nv)) / base * 100.0 > tol_pct:
            return False
    return True


def run_case(session: requests.Session, case: Case, strict: bool,
             lag_info: dict[str, Any] | None = None) -> dict[str, Any]:
    t0 = time.perf_counter()
    old = fetch(session, case.old_base, case.old_path, case.old_params)
    new = fetch(session, NEW_8810, case.new_path, case.new_params)
    elapsed = round((time.perf_counter() - t0) * 1000, 1)

    rec: dict[str, Any] = {
        "domain": case.domain,
        "name": case.name,
        "old_url": old["url"],
        "new_url": new["url"],
        "old_status": old["status"],
        "new_status": new["status"],
        "old_ms": old["ms"],
        "new_ms": new["ms"],
        "elapsed_ms": elapsed,
        "status": "pass",
        "old_retired": False,
        "diff_count": 0,
        "unexplained": 0,
        "precision_fp32": 0,
        "version_drift": [],
        "ignored_old_keys": [],
        "extra_new_keys": [],
        "uncovered_old_fields": [],
        "samples": [],
        "error": None,
    }

    if not old["ok"] and _is_conn_refused(old.get("error")):
        # 2026-11-09：旧 8800/8002 已退役（A0 §一：2026-10-30 三端口下线并销毁源文件）。
        # 旧端拒绝连接 = 退役，不是故障 → 降为「单端自检」：只要求新端 200 + JSON 可解析。
        # 注意：这只能兜住「旧端真不在了」；新端路径失效（404）仍照常判 error，不许被掩盖。
        rec["old_retired"] = True
        if new["ok"]:
            rec["status"] = "pass"
        else:
            rec["status"] = "error"
            rec["error"] = {"old": None, "new": new.get("error")}
            rec["new_body"] = new.get("body")
        return rec

    if not old["ok"] or not new["ok"]:
        rec["status"] = "error"
        rec["error"] = {
            "old": None if old["ok"] else old.get("error"),
            "new": None if new["ok"] else new.get("error"),
        }
        rec["old_body"] = old.get("body")
        rec["new_body"] = new.get("body")
        return rec

    cmp = COMPARATORS[case.comparator](case, old["data"], new["data"])
    diffs = cmp["diffs"]
    fp32 = cmp["precision_fp32"]

    # 已知旧端缺陷路径：命中即从 diffs 移出，单列到 known_old_defects（不判 fail，但必须可见）
    known: list[dict[str, Any]] = []
    if case.known_old_defect_patterns and diffs:
        patterns = [re.compile(p) for p in case.known_old_defect_patterns]
        # 已知缺陷代码范围：从**旧端响应**里把 rows[i] 映射到股票代码，
        # 这样 `rows[16].bull` 能判断「16 号行到底是不是 920xxx」。
        allowed_codes = case.known_old_defect_codes
        row_codes = _row_codes(old["data"]) if allowed_codes else {}
        old_rows_n = len((old["data"].get("rows") or [])) if isinstance(old["data"], dict) else 0

        def _satisfies_requires(d: dict[str, Any]) -> bool:
            """已知缺陷的额外自证条件；没命中就不得当已知缺陷。"""
            for req in case.known_old_defect_requires:
                if req == "n_total_over_rows":
                    ov = d.get("old")
                    if not _is_num(ov) or ov <= old_rows_n:
                        return False
                elif req == "old_null_new_value":
                    # 只允许「旧端无值、新版有值」这个方向（旧端数据源缺口）。
                    # 反向（旧有值、新版 null）是新版退化，**必须**判 fail —— 不能拿它掩盖回归。
                    if d.get("old") is not None or d.get("new") is None:
                        return False
            return True

        def _in_known_scope(path: str) -> bool:
            if not allowed_codes:
                return True          # 未声明代码范围 = 不限（调用方自己负责）
            m = re.match(r"^rows\[(\d+)\]\.", path)
            if not m:
                return True          # 非行级字段（如 n_total）由正则自己把关
            code = row_codes.get(int(m.group(1)))
            if not code:
                return False         # 定位不到代码 → 不当已知缺陷，宁可判 fail
            return any(str(code).startswith(c) or str(c) in str(code) for c in allowed_codes)

        keep: list[dict[str, Any]] = []
        for d in diffs:
            path = str(d.get("path") or "")
            if (any(p.match(path) for p in patterns)
                    and _in_known_scope(path)
                    and _satisfies_requires(d)):
                known.append(d)
            else:
                keep.append(d)
        diffs = keep

    # 并列名次容忍：变化量相等的股票名次互换是定义上就无先后的，不能算差异。
    # 只处理 `rows[i].sec_rank` / `rows[i].trail`；按「日期→名次多重集」判定，
    # 多重集相同 = 只是并列组内互换 → 放过；否则照常判 fail。
    tie_skipped: list[dict[str, Any]] = []
    if case.tie_aware_rank and diffs and isinstance(old["data"], dict) \
            and isinstance(new["data"], dict):
        diffs, tie_skipped = _drop_tie_permutations(diffs, old["data"], new["data"])

    # 「旧端无值→新版有值」可放行的字段（只这一个方向；反向照判 fail）。
    old_gaps: list[dict[str, Any]] = []
    if case.old_null_ok and diffs:
        pats = [re.compile(p) for p in case.old_null_ok]
        keep_old: list[dict[str, Any]] = []
        for d in diffs:
            leaf = str(d.get("path", "")).rsplit(".", 1)[-1]
            if any(p.search(leaf) for p in pats) and d.get("old") is None and d.get("new") is not None:
                old_gaps.append(d)
            else:
                keep_old.append(d)
        diffs = keep_old

    # 「日线源滞后」可解释的差异：新版 null、旧端有值，且字段在 lag_explainable 里。
    # 只在探测到真滞后时才生效（lag_info.lag），否则照常判 fail。
    lag_explained: list[dict[str, Any]] = []
    if case.lag_explainable and diffs and (lag_info or {}).get("lag"):
        pats = [re.compile(p) for p in case.lag_explainable]
        keep_diff: list[dict[str, Any]] = []
        for d in diffs:
            path = str(d.get("path", ""))
            leaf = path.rsplit(".", 1)[-1]
            if (any(p.search(path) or p.search(leaf) for p in pats)
                    and d.get("new") is None and d.get("old") is not None):
                lag_explained.append(d)
            else:
                keep_diff.append(d)
        diffs = keep_diff

    # 盘中实时字段：只差一个跳价时降为 live_skew（不判 fail，但报告里可见）。
    live_skew: list[dict[str, Any]] = []
    if _live_skew_only(diffs, case.live_fields, case.live_tol_pct):
        live_skew = diffs
        diffs = []

    # 旧端有、新版没有的字段：未声明允许缺失的已经记入 missing_in_new → 计入 diffs
    missing = cmp.get("missing_in_new") or []
    for sub in missing:
        diffs.append({"path": sub, "kind": "missing_in_new"})

    unexplained = len(diffs) + (fp32 if strict else 0)

    rec["diff_count"] = len(diffs) + fp32
    rec["unexplained"] = unexplained
    rec["precision_fp32"] = fp32
    rec["known_old_defects"] = known[:MAX_SAMPLES]
    rec["known_old_defect_count"] = len(known)
    rec["tie_permutations"] = tie_skipped[:MAX_SAMPLES]
    rec["tie_permutation_count"] = len(tie_skipped)
    rec["precision_price"] = cmp.get("precision_price", 0)
    rec["version_drift"] = cmp["version_drift"]
    rec["ignored_old_keys"] = cmp["ignored_old_keys"][:MAX_SAMPLES]
    rec["ignored_old_keys_total"] = len(cmp["ignored_old_keys"])
    rec["extra_new_keys"] = cmp["extra_new_keys"][:MAX_SAMPLES]
    rec["extra_new_keys_total"] = len(cmp["extra_new_keys"])
    rec["uncovered_old_fields"] = cmp["uncovered_old_fields"]
    rec["samples"] = diffs[:MAX_SAMPLES]
    rec["live_skew"] = live_skew[:MAX_SAMPLES]
    rec["live_skew_count"] = len(live_skew)
    rec["source_lag"] = {"count": len(lag_explained), "samples": lag_explained[:MAX_SAMPLES],
                         "new_last": (lag_info or {}).get("new_last"),
                         "old_last": (lag_info or {}).get("old_last")}
    rec["old_gaps"] = {"count": len(old_gaps), "samples": old_gaps[:MAX_SAMPLES]}
    if "bars" in cmp:
        rec["bars"] = cmp["bars"]

    if unexplained > 0:
        # 版本键不同 + 存在差异 → 很可能是两次请求之间数据更新了，不算 fail。
        rec["status"] = "skew" if cmp["version_drift"] else "fail"
    elif live_skew:
        # 只差盘中跳价：不判 fail，但报告里显式可见，不隐藏。
        rec["status"] = "live_skew"
    elif lag_explained:
        # 差异全由「新版日线仍停在 T-1」解释：不判 fail，但单列可见。
        rec["status"] = "source_lag"
    elif old_gaps:
        # 差异全是「旧端无值、新版有值」：旧端数据源缺口，单列可见。
        rec["status"] = "old_gap"
    return rec


def detect_source_lag(session: requests.Session) -> dict[str, Any]:
    """探测「新版日线比旧端晚一天」（数据源时序差）。

    背景（2026-10-23 实测）：16:10 主链 **16:40 写 vipdoc**（旧 8002 读它），
    而 `track_pool.db::kline` 要等 **18:00 灌库**。所以 16:40–18:00 这段复盘黄金时段，
    新版日线仍停在 T-1，旧端已能拿到当日收盘：
    → `real_pct` / `diverge` 这类「当日收盘参与计算」的字段，新版必然 null。

    这不是逻辑错误（null 比编造值诚实），但必须**用证据区分**，否则 shadow 会误判。
    返回 `{lag: bool, new_last, old_last}`。
    """
    out: dict[str, Any] = {"lag": False, "new_last": None, "old_last": None}
    try:
        n = fetch(session, NEW_8810, "/api/p/kline/bars/000001.SH", {"limit": "1"})
        o = fetch(session, OLD_8800, "/api/kline/000001.SH", {"period": "d", "limit": "1"})
        nb = (n.get("data") or {}).get("bars") or []
        ob = (o.get("data") or {}).get("bars") or []
        nl = str(nb[-1].get("date")) if nb else None
        ol = str(ob[-1].get("t"))[:8] if ob else None
        out["new_last"], out["old_last"] = nl, ol
        out["lag"] = bool(nl and ol and nl < ol)
    except Exception:  # noqa: BLE001 - 探测失败就当无 lag
        pass
    return out


def run_round(session: requests.Session, cases: list[Case], strict: bool) -> dict[str, Any]:
    t0 = time.perf_counter()
    started = datetime.now()
    lag_info = detect_source_lag(session)
    results = []
    for case in cases:
        try:
            results.append(run_case(session, case, strict, lag_info))
        except Exception as e:  # 单例异常不打断整轮
            results.append({
                "domain": case.domain, "name": case.name,
                "old_url": case.old_base + case.old_path,
                "new_url": NEW_8810 + case.new_path,
                "status": "error",
                "error": f"{type(e).__name__}: {e}",
                "traceback": traceback.format_exc(limit=3),
                "diff_count": 0, "unexplained": 0, "precision_fp32": 0,
                "version_drift": [], "ignored_old_keys": [], "extra_new_keys": [],
                "uncovered_old_fields": [], "samples": [],
            })

    domains: dict[str, dict[str, Any]] = {}
    for r in results:
        d = domains.setdefault(r["domain"], {"status": "pass", "cases": [], "fail": 0, "skew": 0, "live_skew": 0,
                                                "source_lag": 0, "old_gap": 0, "error": 0})
        d["cases"].append(f"{r['name']}:{r['status']}")
        if r["status"] == "fail":
            d["fail"] += 1
        elif r["status"] == "skew":
            d["skew"] += 1
        elif r["status"] == "live_skew":
            d["live_skew"] += 1
        elif r["status"] == "source_lag":
            d["source_lag"] += 1
        elif r["status"] == "old_gap":
            d["old_gap"] += 1
        elif r["status"] == "error":
            d["error"] += 1
    for d in domains.values():
        if d["fail"]:
            d["status"] = "fail"
        elif d["error"]:
            d["status"] = "error"
        elif d["skew"]:
            d["status"] = "skew"
        elif d["live_skew"]:
            d["status"] = "live_skew"

    counts = {"pass": 0, "fail": 0, "skew": 0, "live_skew": 0,
              "source_lag": 0, "old_gap": 0, "error": 0}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    return {
        "tool": "shadow_check",
        "schema": "shadow_check/1",
        "started_at": started.strftime("%Y-%m-%d %H:%M:%S"),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "strict": strict,
        "tolerance": TOL,
        "endpoints": {"old_8800": OLD_8800, "old_8002": OLD_8002, "new_8810": NEW_8810},
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        "summary": {
            "total": len(results),
            **counts,
            # live_skew 只在盘中出现（旧端现算、新版读文件，差一个跳价），不阻塞退出码。
            "exit_code": 1 if counts["fail"] or counts["error"] else 0,
        },
        "domains": domains,
        "cases": results,
    }


def write_report(payload: dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"{stamp}.json"
    n = 1
    while path.exists():
        path = out_dir / f"{stamp}_{n}.json"
        n += 1
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def print_summary(payload: dict[str, Any], path: Path, tag: str = "") -> None:
    s = payload["summary"]
    parts = [f"{k}={s[k]}" for k in ("pass", "fail", "skew", "live_skew", "source_lag", "old_gap", "error") if s.get(k)]
    bad = [f"{r['domain']}.{r['name']}" for r in payload["cases"] if r["status"] in ("fail", "error")]
    line = f"{tag}{payload['generated_at']}  {' '.join(parts)}  {payload['elapsed_ms']}ms"
    if bad:
        line += "  ↯ " + ",".join(bad)
    print(line)
    print(f"  -> {path}")


# ---------------------------------------------------------------- 主流程

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="M4 shadow 双跑：旧端 vs 新 8810")
    ap.add_argument("--once", action="store_true", help="跑一轮（默认）")
    ap.add_argument("--loop", action="store_true", help="循环跑，配合 --interval")
    ap.add_argument("--interval", type=int, default=300, help="循环间隔秒，默认 300，最小 30")
    ap.add_argument("--strict", action="store_true", help="float32 精度差异也计入 fail")
    ap.add_argument("--domains", default="", help="只跑指定域，逗号分隔：sector,amv,battle_plan,kline")
    ap.add_argument("--out-dir", default="", help="覆盖输出目录（默认项目内 data/panel_v2/shadow）")
    args = ap.parse_args(argv)

    if args.loop and args.interval < MIN_INTERVAL:
        print(f"--interval 不能小于 {MIN_INTERVAL} 秒", file=sys.stderr)
        return 2

    domains = [d.strip() for d in args.domains.split(",") if d.strip()]
    cases = CASES
    if domains:
        unknown = set(domains) - {c.domain for c in CASES}
        if unknown:
            print(f"未知域：{sorted(unknown)}", file=sys.stderr)
            return 2
        cases = [c for c in CASES if c.domain in domains]

    out_dir = Path(args.out_dir) if args.out_dir else OUT_DIR
    session = requests.Session()
    session.headers.update({"User-Agent": "panel-v2-shadow-check/1", "Accept": "application/json"})

    if not args.loop:
        payload = run_round(session, cases, args.strict)
        path = write_report(payload, out_dir)
        print_summary(payload, path)
        return payload["summary"]["exit_code"]

    # 循环模式：前台跑，Ctrl-C 退出。任何一轮出现 fail/error 记入累计退出码。
    print(f"shadow loop 启动：{len(cases)} 项，间隔 {args.interval}s，Ctrl-C 结束")
    worst = 0
    i = 0
    try:
        while True:
            i += 1
            payload = run_round(session, cases, args.strict)
            path = write_report(payload, out_dir)
            print_summary(payload, path, tag=f"[{i}] ")
            worst = max(worst, payload["summary"]["exit_code"])
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print(f"\n结束：共 {i} 轮")
        return worst


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    sys.exit(main())
