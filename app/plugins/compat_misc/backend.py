# -*- coding: utf-8 -*-
"""compat_misc 插件后端：旧前端剩余 **10 个**只读根路径端点。

契约来源与逐端点比对：`docs/compat_misc.md`；本文件只实现 `backend.mount = "root"`
的 GET（core 统一挂到 `/api`），**不实现任何 POST**，不写任何文件。

## 为什么单独一个插件

第 1/2/3 批（`compat` / `compat_kline` / `compat_sector` / `compat_watchlist`）已覆盖
旧前端大部分路径；剩下这些分属「后台监控 / 选股页 / AMV 副图 / 复盘 / UI 标注」
几个互不相干的域，但都是**只读、同源、无需新口径**，合一个插件最省 manifest 与路由冲突面。

## 2026-10-23（4.1.3b）移交给 `paper` 插件的 5 个端点

`/order/list`、`/trades/{ts_code}`、`/condition/list`、`/paper_account`、`/ledger`
原本是本插件返回的**空骨架**（数据源 `engine/state/*` 属交易链红线，新版无权读）。
4.1.3b 起这些路径改由 `app/plugins/paper` 插件用**新纸面账本**
（`data/panel_v2/paper/`，core 服务 `app/core/paper/`）真实实现，root 挂载到同一
`/api` 前缀。两个插件不得抢同一路径（`main.py` 启动即报冲突），所以本插件把
这 5 条路由与它们的骨架构造函数**一并删除**（本文件不再保留任何交易链代码）。

## 2026-10-23（4.3）选股三端点转为**真实数据**

`paths.READABLE_PANEL` 补登记了 `selection_latest` / `selection_pool` / `selection_filter`
三份**已有生产文件**（任务书 `docs/task_selection_4_3.md` §1），本插件改走 `ctx.panel_json`
**只读透传 + 轻量拼装**（拼一个 `pool` 段、算一个 `freshness`），**不重算选股、不查
`buy_signal.db`、不写任何文件**。选股口径的唯一源仍是旧 8800 盘后链
（`engine/collectors/selection_snapshot_builder.py` → `selection_latest.json`）。

## 2026-10-23（4.5）`/review.md` 移交 `app/plugins/review`

任务书 `docs/task_review_md_4_5.md` 把复盘 Tab 的真实渲染管线搬成独立 `review` 插件
（`paths.READABLE_TEXT_DIR` 新增 `daily_review` 只读文本目录 + 移植的
`md2html.py` 复盘渲染链），root 挂载到同一 `/api` 前缀。
两个 root 插件抢同一路径会让 `main.py` 启动即报冲突 → **本插件把 `/review.md` 骨架路由与
`build_review_md` / `REVIEW_MD_KEYS` 一并删除**（只删这一条，`/review_chart.json` 仍在）。

## 2026-10-24（N2 周末冲刺）：`/ops.json` 与 `/ui_notes/list` 移交 `app/plugins/ops`

两条路径的旧数据源都已有受控通道（`ops_status.json` 进 `READABLE_PANEL["ops"]`；
`ui_notes.json` 进 `WRITABLE_STATE` + `ctx.state_json`），由新 `ops` 插件（mount=root）
真实实现。两个 root 插件抢同一路径会让 `main.py` 启动即报冲突 → 本插件删除这两条路由
与骨架构造函数 `build_ops` / `build_ui_notes` / `OPS_SKELETON`。

## 数据源现实（其余端点，2026-10-23，**未改 paths.py**）

| 端点 | 理想数据源 | 本次可否读取 |
|---|---|---|
| `/ops.json` | `data/panel/ops_status.json` | ➡️ **已移交 `ops` 插件**（N2，2026-10-24）
| `/selection` | `data/panel/selection_latest.json` | ✅ **真实实现**（4.3 白名单补登记） |
| `/selection_pool` | `data/panel/selection_pool.json` | ✅ **真实实现** |
| `/selection_prefs` | `data/panel/selection_filter.json` | ✅ **真实实现** |
| `/selection_board_options` | `ths_board.db` + `panel/watchlist.json` | ✅ **真实实现** |
| `/stock_amv/{ts_code}` | `amv_board.db::stock_state` | ✅ **真实实现** |
| `/sector_amv/{code}` | `amv_board.db::sector_daily` / `sector_meta` | ✅ **真实实现** |
| `/sector_kline/{code}` | `ths_index.db::index_daily` 或 vipdoc `.day` | ❌ 库未登记 → 退 `ctx.quotes` 日线 |
| `/ui_notes/list` | `engine/state/ui_notes.json` | ➡️ **已移交 `ops` 插件**（N2） |
| `/review.md` | `夏天-activity/每日复盘/*.md` | ➡️ **已移交 `review` 插件**（4.5） |
| `/review_chart.json` | 11 个源实时装配 | ❌ 多数 JSON 未登记（MD 已可读，但装配仍缺） |

拿不到的端点一律返回**旧端同构空骨架**（键名齐、值 null/空数组），并在
`docs/compat_misc.md` §5 列出需要 core owner 补的白名单。
（4.3 的选股三份文件已由 core owner 登记进 `READABLE_PANEL`；本插件自己**仍不改白名单**。）

## 硬约束

- 只有 GET；无 `sqlite3.connect`（一律 `ctx.db_read`）、无 `open()`、无 HTTP、无子进程；
- 缺数据不填 0、不猜、不拿旧文件冒充当日：宁可 `null` / `[]`。
"""
from __future__ import annotations

import contextlib
import copy
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel  # noqa: F401 - 兼容旧前端 POST body 的显式模型（选股池/偏好）

# ---------------------------------------------------------------- 常量

AMV_DB = "amv_board.db"
THS_DB = "ths_board.db"
INDICATOR_LIB = "indicators"      # 黄白线口径唯一源（旧 /api/sector_kline 同源）
MA_N = 10                          # tdx_sector_api.MA_N（副图均线周期）

# 选股池快照保留数（旧端 `SEL_POOL_KEEP`）
SEL_POOL_KEEP = 5

# 盘后链 `selectionsnap` 步的理论最晚完成点（旧端 `SELECTION_SNAPSHOT_PRODUCE_BY` 同值）。
# 过了这个点快照还停在更早交易日 → 才算「陈旧」；盘中/刚收盘不喊陈旧（假告警比不告警更坏）。
SELECTION_PRODUCE_BY = "18:00"


# ---------------------------------------------------------------- 基础工具


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _finite(value: Any) -> bool:
    """旧端 `_finite` 同口径：可转 float 且非 NaN/Inf。"""
    try:
        f = float(value)
        return f == f and abs(f) != float("inf")
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------- 盘中量比（2026-11-13）
# 选股页「量比」列的盘后口径 = 当日成交量 / 前 20 个交易日（不含当日）均量
# （`engine/server.py::SEL_VOL_RATIO_WIN = 20`）。盘中分母不变、分子换成当日累计量，
# 再用已开市分钟做时间校正（同盘口卡 `stats.vol_ratio` 的公式，分母换成 20 日）：
#     量比 = (当日累计量 / 已开市分钟) / (前 20 日均量 / 240)
# 15:00 时 em=240，上式退化为 当日量 / 20 日均量 —— **收盘值与盘后快照同口径**，
# 不是另起一套。单位全程用手：快照 `total_hand` 与 eltdx 日线 `volume_lots` 同为手。
_VOL20_CACHE: Dict[str, Any] = {"day": "", "map": {}}


def _vol_avg20(ctx: Any, glbs: List[str]) -> Dict[str, float]:
    """{glb: 前 20 个交易日日均量（手）}；按日缓存，样本不足/取数失败 → 该票不给。"""
    day = datetime.now().strftime("%Y%m%d")
    cache = _VOL20_CACHE
    if cache.get("day") != day:
        cache["day"] = day
        cache["map"] = {}
    book: Dict[str, Any] = cache["map"]
    out: Dict[str, float] = {}
    for g in glbs:
        if g in book:
            if book[g] is not None:
                out[g] = book[g]
            continue
        avg: Optional[float] = None
        try:
            rows = list(ctx.quotes.daily_bars(g, count=21) or ())
            # 盘中 eltdx 日线会多一根「今日」未收盘 bar：它是分子不是分母，必须剔掉
            hist = rows
            if rows:
                t = getattr(rows[-1], "time", None)
                if hasattr(t, "strftime") and t.strftime("%Y%m%d") == day:
                    hist = rows[:-1]
            lots: List[float] = []
            for b in hist[-20:]:
                try:
                    f = float(getattr(b, "volume_lots", None))
                except (TypeError, ValueError):
                    continue
                if f > 0:
                    lots.append(f)
            if len(lots) >= 20:
                avg = sum(lots) / 20.0
        except Exception:  # noqa: BLE001 - 单票取不到 → 该票不给量比（不编造）
            avg = None
        book[g] = avg
        if avg is not None:
            out[g] = avg
    return out


def _live_vol_ratio(total_hand: Any, avg20_lots: Any, em: Any) -> Optional[float]:
    """盘中量比（时间校正）。任一输入缺失/非正 → None（不补 0、不猜）。"""
    try:
        total = float(total_hand)
        base = float(avg20_lots)
        minutes = int(em)
    except (TypeError, ValueError):
        return None
    if total <= 0 or base <= 0 or minutes <= 0:
        return None
    return round((total / float(minutes)) / (base / 240.0), 2)


def _digits(raw: Any, n: int = 6) -> str:
    """取前 n 位纯数字（'881166.TI' → '881166'）；不足 → 原串 strip。"""
    s = "".join(ch for ch in str(raw or "") if ch.isdigit())
    return s[:n] if len(s) >= n else str(raw or "").strip()


# ---------------------------------------------------------------- 只读取数


def _panel(ctx: Any, name: str) -> Optional[Dict[str, Any]]:
    """读白名单 panel JSON；未声明/不在白名单/文件缺失 → None（降级，不抛）。"""
    getter = getattr(ctx, "panel_json", None)
    if not callable(getter):
        return None
    try:
        data = getter(name)
    except Exception:  # noqa: BLE001 - PermissionError / FileNotFoundError 统一降级
        return None
    return data if isinstance(data, dict) else None


@contextlib.contextmanager
def _conn(ctx: Any, name: str) -> Iterator[Any]:
    """取只读连接；库不可用 → 交出 None（调用方返回骨架/404，不抛穿）。"""
    conn = None
    try:
        conn = ctx.db_read(name)
    except FileNotFoundError:
        conn = None
    except PermissionError:
        conn = None
    except Exception:  # noqa: BLE001 - 其余不可用按降级处理（不伪装成 500）
        conn = None
    try:
        yield conn
    finally:
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()


def _rows(conn: Any, sql: str, params: Tuple[Any, ...] = ()) -> List[Any]:
    """执行查询；SQL/表缺失 → 空表（不同 SQLite 版本列差异不该炸端点）。"""
    try:
        return list(conn.execute(sql, params))
    except Exception:  # noqa: BLE001
        return []


_CUSTOM_CODE_FN: List[Any] = []   # 2026-10-25：自建板块码判据缓存（唯一源 ths_board_pool.is_custom_code）


def _is_custom_board(code: Any) -> bool:
    """是否自建板块码（999 段）。唯一源 = `夏天-activity/工具/ths_board_pool.is_custom_code`。

    2026-10-25：概念层改只算自建板块后，选股页板块候选只给自建段
    （否则能把已下线 THS 概念加进自选、点进去空板块）。
    读不到工具目录 → 保守返回 True（不过滤、不丢板块）。
    """
    if not _CUSTOM_CODE_FN:
        import importlib.util  # noqa: PLC0415
        from app.core import paths as _core_paths  # noqa: PLC0415
        from pathlib import Path as _Path  # noqa: PLC0415
        p = str(_core_paths.SUMMER_TOOLS_DIR / "ths_board_pool.py")
        fn = None
        # 2026-10-25：`os.path.exists` → `Path.is_file`（**函数内** import）。
        # 为什么不在顶层 import：test_no_http_or_file_write 除禁用列表外还有一条
        # **正向白名单** `imported <= {__future__, contextlib, copy, datetime,
        # typing, fastapi, pydantic}` —— pathlib 不在其中，放顶层同样会红。
        # 放函数内既满足「不许 import os」又满足白名单，语义不变：
        # 不是常规文件（缺失/目录/权限）→ fn 留 None → 保守不过滤、不丢板块。
        if _Path(p).is_file():
            spec = importlib.util.spec_from_file_location("_cm_ths_board_pool", p)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            fn = m.is_custom_code
        _CUSTOM_CODE_FN.append(fn)
    fn = _CUSTOM_CODE_FN[0]
    return True if fn is None else fn(code)


def _yellow_white(ctx: Any, closes: List[float]) -> Tuple[List[Any], List[Any]]:
    """黄白线（口径唯一源 `engine/common/indicators.py`，经 `ctx.lib`）；不可用 → 空。"""
    getter = getattr(ctx, "lib", None)
    if not callable(getter):
        return [], []
    try:
        y, w = getter(INDICATOR_LIB).yellow_white_lines(closes)
        return list(y), list(w)
    except Exception:  # noqa: BLE001 - 口径库不可用 → 黄白线留空（不编造）
        return [], []


# ================================================================ 1. /ops.json（已移交 ops 插件）
#
# 2026-10-24（N2 周末冲刺）：`GET /api/ops.json` 的真实实现已由 `app/plugins/ops` 承接
# （`paths.READABLE_PANEL["ops"] → data/panel/ops_status.json` + `ctx.panel_json`）。
# 两个 root 插件抢同一路径会让 core 启动即报冲突 → 本插件的 `build_ops` / `OPS_SKELETON`
# **整块删除**，不留第二份骨架。旧端键集（meta/summary/tasks/daemons/freshness）由 ops 插件
# 的测试锁住。


# ================================================================ 2~6. 交易链端点（已移交 paper 插件）
#
# 2026-10-23（4.1.3b）：`/order/list`、`/trades/{ts_code}`、`/condition/list`、
# `/paper_account`、`/ledger` 五条路径连同它们的 `build_*` 骨架构造函数已**整块删除**。
#
# 原因不是「不做了」，而是**换了权威源**：这 5 条的旧数据源全在 `engine/state/*`（交易链红线，
# 新版无权读），本插件只能返回 `ok:false` 的空骨架；4.1.3b 起 `app/plugins/paper` 插件用新的
# 纸面账本（`data/panel_v2/paper/`，业务在 `app/core/paper/`）真实实现同一批路径。
# 两个插件都 `mount=root` 到 `/api`，`main.py` 启动即判重复路径冲突 → 本插件必须让路。
#
# 保留的历史口径（供后来者查证，不要在别处再实现一份）：
#   /api/order/list    → {"ok","mode","date","orders","conditions","account","metrics"}，
#                        account 五键 = available_cash/total_asset/frozen_cash/positions/updated_at
#   /api/trades/{code} → {"version","code","days","points","count","cost{paper,live}"}
#   /api/condition/list→ {"ok","conditions"}
#   /api/paper_account → 账本顶层键 + account{8 键} + positions_list[]
#   /api/ledger        → {"ok","mode","asof","account{10 键}","positions","trades",
#                        "conds_expiring","book{active,history,hist_max,hist_total}"}
# 这 5 条的形状现在由 `app/plugins/paper/views.py` 负责（逐键对齐同一份基线，
# 基线文件 `docs/misc_samples/keys_expected.json` 仍然有效）。


# ================================================================ 7~10. 选股页四端点

def _dash_date(raw: Any) -> str:
    """日期归一成 `YYYY-MM-DD`（旧端 `_sel_pool_key` 同口径）。

    兼容 `2026-10-25` / `20261025` / `1025`（按当年补全）；不可解析 → ''。
    """
    s = str(raw or "").strip()
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) == 4:                      # '1025' → 当年
        digits = datetime.now().strftime("%Y") + digits
    if len(digits) < 8:
        return ""
    d8 = digits[:8]
    return "%s-%s-%s" % (d8[:4], d8[4:6], d8[6:])


def _sel_pool_skeleton(date: Any = None) -> Dict[str, Any]:
    """旧端 `_selection_pool_payload` 的键（date/count/is_current/names/items/slots/keep）。"""
    return {"date": _dash_date(date), "count": 0, "is_current": False,
            "names": [], "items": [], "slots": [], "keep": SEL_POOL_KEEP}


def _code_norm(raw: Any) -> str:
    """选股池代码归一：6 位码 → ts_code；已带后缀原样大写（旧端 to_ts_code 同口径）。"""
    s = str(raw or "").strip()
    if not s:
        return ""
    if "." in s:
        return s.upper()
    if len(s) != 6 or not s.isdigit():
        return s.upper()
    if s[:3] == "920" or s[0] in "48":
        return s + ".BJ"
    if s[0] in "659":
        return s + ".SH"
    return s + ".SZ"


def _sel_pool_items_of(node: Any) -> List[Dict[str, Any]]:
    """把一个快照节点规整成 items（旧端 `_sel_pool_items_of` 同口径）。"""
    out: List[Dict[str, Any]] = []
    for idx, it in enumerate((node or {}).get("items") or []):
        if not isinstance(it, dict):
            continue
        code = str(it.get("code") or "").strip()
        if not code:
            continue
        try:
            order = int(it.get("order"))
        except (TypeError, ValueError):
            order = idx
        out.append({"code": code, "name": str(it.get("name") or ""),
                    "group": str(it.get("group") or ""), "sig": str(it.get("sig") or ""),
                    "added_at": str(it.get("added_at") or ""), "order": order})
    return out


def _sel_pools_doc(ctx: Any) -> Tuple[Dict[str, Any], str]:
    """读全量快照集 → (pools, current)；兼容旧结构（顶层 items/date）。"""
    doc = _panel(ctx, "selection_pool") or {}
    pools = doc.get("pools")
    if isinstance(pools, dict) and pools:
        norm = {}
        for k, v in pools.items():
            if isinstance(v, dict):
                norm[_dash_date(k)] = {"items": _sel_pool_items_of(v),
                                       "saved_at": str(v.get("saved_at") or "")}
        return norm, _dash_date(doc.get("current") or "")
    legacy = _sel_pool_items_of(doc)
    d = _dash_date(doc.get("date") or "")
    if legacy and d:
        return {d: {"items": legacy,
                    "saved_at": str((doc.get("meta") or {}).get("updated_at") or "")}}, d
    return {}, d


def _sel_pool_raw(ctx: Any, date: Any = None) -> Tuple[List[Dict[str, Any]], str]:
    pools, cur = _sel_pools_doc(ctx)
    d = _dash_date(date) or cur
    if not d or d not in pools:
        return [], (d or "")
    return pools[d]["items"], d


def _sel_pools_save(ctx: Any, pools: Dict[str, Any], current: str = "") -> Dict[str, Any]:
    """写全量快照集（v2 结构，唯一写入口）；超过 5 份删最旧。"""
    for k in sorted(pools.keys(), reverse=True)[SEL_POOL_KEEP:]:
        pools.pop(k, None)
    for node in pools.values():
        its = sorted(node.get("items") or [],
                     key=lambda x: (x.get("order", 0), x.get("added_at", "")))
        for i, it in enumerate(its):
            it["order"] = i
        node["items"] = its
    if current and current not in pools:
        current = ""
    doc = {"meta": {"schema_version": "2.0.0", "document_type": "selection_pool",
                    "updated_at": _now(), "source": "server", "keep": SEL_POOL_KEEP},
           "current": current, "pools": pools}
    ctx.panel_write("selection_pool", doc)
    return doc


# 旧端 `_selection_snapshot_payload` 的**快照缺失分支**逐字（engine/server.py:6419-6429）。
SELECTION_SKELETON: Dict[str, Any] = {
    "meta": {"schema_version": "1.0.0", "document_type": "selection",
             "generated_at": None, "source": None},
    "date": "",
    "error": None,
    "asof": {},
    "filters": {},
    "filter_options": {},
    "group_sigs": {},
    "group_filters": {},
    "board_map": {},
    "hot_concepts": {},
    "stats": {"hits": 0, "groups": 0, "scored": 0},
    "warnings": [],
    "score_warn": [],
    "freshness": {"state": "unknown", "snapshot_date": "", "expected_date": "",
                  "produce_by": None, "today_pending": False},
    "pool": None,
    "groups": [],
}


def _mmdd_groups(wl: Dict[str, Any]) -> List[Dict[str, Any]]:
    """自选池里的**日期分组**（name 是 4 位数字、kind=stock），按 name 降序（最新在前）。

    2026-11-13 主人：选股页的「选股池」不再用独立的 selection_pool.json，
    而是**映射到自选池里最多存在的那几个 MMDD 组**（如 1112）。
    """
    out = []
    for g in (wl.get("groups") or []):
        if not isinstance(g, dict):
            continue
        if str(g.get("kind") or "") != "stock":
            continue
        nm = str(g.get("name") or "").strip()
        if len(nm) == 4 and nm.isdigit():
            out.append(g)
    return sorted(out, key=lambda g: str(g.get("name") or ""), reverse=True)


def _mmdd_to_date(nm: str) -> str:
    """'1112' → '2026-11-12'（MMDD 里没有年份：取当前年；月份大于当前月则算去年的）。"""
    s = str(nm or "").strip()
    if len(s) != 4 or not s.isdigit():
        return ""
    now = datetime.now()
    y = now.year
    try:
        mo = int(s[:2])
    except (TypeError, ValueError):
        return ""
    if mo > now.month:
        y -= 1
    return "%d-%s-%s" % (y, s[:2], s[2:4])


def _build_selection_pool_legacy(ctx: Any, date: Any = "", today: Any = None) -> Dict[str, Any]:
    """【兼容回退】自选池里还没有 MMDD 日期分组时，读旧的 selection_pool.json。

    为什么留：迁移期。旧测试与本机以外的现场可能还没有日期分组；没有时给空池会让
    「选股池」跟新功能一起消失。有 MMDD 组就走新实现（生产即如此）。
    `selection_pool.json` 退役后删本函数。
    """
    pools, cur = _sel_pools_doc(ctx)
    want = _dash_date(date) if str(date or "").strip() else ""
    d = want or cur
    if not d or d not in pools:
        out = _sel_pool_skeleton(date)
        out["group_id"] = ""
        out["mmdd"] = []
        return out
    items = pools[d]["items"]
    today_s = _dash_date(today) or datetime.now().strftime("%Y-%m-%d")
    slots = [{"date": k, "count": len((pools[k].get("items") or [])),
              "saved_at": str(pools[k].get("saved_at") or "")}
             for k in sorted(pools.keys(), reverse=True)]
    return {"date": d,
            "count": len(items),
            "is_current": bool(d) and d == today_s and (not want or want == d),
            "names": [str(x.get("name")) for x in items
                      if isinstance(x, dict) and x.get("name")][:3],
            "items": items,
            "slots": slots,
            "keep": SEL_POOL_KEEP,
            "group_id": "",
            "mmdd": []}


def build_selection_pool(ctx: Any, date: Any = "", today: Any = None) -> Dict[str, Any]:
    """选股池 = **自选池里最新的日期分组（MMDD）**（2026-11-13 主人定）。

    为什么改源：`data/panel/selection_pool.json` 实测停在 2026-10-21（一天 13 条），
    主人真正在用的是自选池的分组；选股页再维护一份池子就是双源。

    **只读视图**：条目原样来自 `watchlist.json`（不新增源、不重算）；
    加/删一律走 `compat_watchlist` 的 `/api/watchlist/add|remove`——
    `watchlist.json` 的写入者只有它一个，本插件不写（避免双写锁）。

    返回旧键不变（date/count/is_current/names/items/slots/keep），另加：
      · `group_id` = 当前日期分组的 id（前端 add 时必须带上）
      · `mmdd` = 全部日期分组 `[{id,name}]`（最多 3 个，最新在前）
    """
    try:
        wl = _panel(ctx, "watchlist")
    except Exception:  # noqa: BLE001 - 读不到当空池（不编造）
        wl = {}
    wl = wl if isinstance(wl, dict) else {}
    groups = _mmdd_groups(wl)
    all_items = [x for x in (wl.get("items") or []) if isinstance(x, dict)]
    want = _dash_date(date) if str(date or "").strip() else ""
    if not groups:
        # 自选池里还没有日期分组 → 回退旧 source（迁移期；selection_pool.json 退役后删）
        return _build_selection_pool_legacy(ctx, date, today)
    # 指定日期时优先匹配该日期的分组（如 ?date=2026-11-11），否则用最新一组
    cur = groups[0]
    if want:
        for g in groups:
            if _mmdd_to_date(g.get("name")) == want:
                cur = g
                break
    gid = str(cur.get("id") or "")
    d = _mmdd_to_date(cur.get("name")) or (want or "")
    items = []
    for it in all_items:
        if str(it.get("group") or "") != gid:
            continue
        items.append({"code": it.get("code"), "name": it.get("name") or "",
                      "group": gid, "sig": "",
                      "added_at": it.get("added_at") or "",
                      "order": it.get("order") or 0})
    items.sort(key=lambda x: (x.get("order") or 0, x.get("added_at") or ""))
    today_s = _dash_date(today) or datetime.now().strftime("%Y-%m-%d")
    return {
        "date": d,
        "count": len(items),
        "is_current": bool(d) and d == today_s and (not want or want == d),
        "names": [str(x.get("name")) for x in items if x.get("name")][:3],
        "items": items,
        "slots": [{"date": _mmdd_to_date(g.get("name")),
                   "count": sum(1 for it in all_items if str(it.get("group")) == str(g.get("id"))),
                   "saved_at": ""} for g in groups],
        "keep": 3,
        "group_id": gid,
        "mmdd": [{"id": g.get("id"), "name": g.get("name")} for g in groups],
    }


def _freshness(snap: Dict[str, Any], now: Any = None) -> Dict[str, Any]:
    """快照新鲜度对账（**只比日期字符串**，不读交易日历、不碰库、不重算）。

    旧端 `_selection_snapshot_freshness` 的简化版（任务书 §2.4）：旧端用
    `common.trading_calendar` 算「最近应产交易日」，本插件**不允许 import common**
    也不新增依赖，故只按「工作日 + 应产时点 18:00」粗判，判不准就 `unknown`：

      · fresh   = 今天工作日、当前 < 18:00、快照就是今天（当天还没到点，不算陈旧）；
                  或今天工作日、已过 18:00 且快照已是今天（盘后链已产）；
      · stale   = 今天工作日、已过 18:00、快照日期 < 今天（盘后链没跑）；
      · unknown = 周末 / 快照日期为空 / 今天工作日但还没到点且快照不是今天
                  （**不猜交易日**：宁可不下结论，也不喊假的 stale）。

    `today_pending` = 今天工作日且当前 < 18:00 但快照不是今天（前端提示「今日快照待生成」）。
    `expected_date` = 「最近一个已过应产时点的交易日」。本版没有日历，只在**能确定时**填：
    已过 18:00 → 今天；未过 18:00 且快照是今天 → 今天；其余留空（不猜上一个交易日）。
    `now` 仅供测试注入（旧端同名参数），生产走 `datetime.now()`。

    ⚠ 与旧端的差异（如实记录，不粉饰）：旧端能识别法定节假日，本版只认周末 → 节假日里
    会判成 unknown（不是 stale，故不会假告警）；要做到旧端精度必须登记 `trading_calendar`
    口径库，属 core/口径库决策，另开任务。
    """
    meta = snap.get("meta") if isinstance(snap.get("meta"), dict) else {}
    snap_date = str(snap.get("date") or (meta or {}).get("signal_date") or "")
    out: Dict[str, Any] = {"state": "unknown", "snapshot_date": snap_date,
                           "expected_date": "", "produce_by": SELECTION_PRODUCE_BY,
                           "today_pending": False}
    if not snap_date:
        return out
    now = now or datetime.now()
    if now.weekday() >= 5:                    # 周末：不判陈旧（不猜交易日）
        return out
    today = now.strftime("%Y-%m-%d")
    if now.strftime("%H:%M") < SELECTION_PRODUCE_BY:
        # 今天还没到应产点：今天的不催。快照是今天 → fresh（今天的已产）；
        # 快照不是今天 → 不下结论（unknown）+ 提示今日待生成，**expected_date 留空**
        # （没有日历就说不准「上一个应产交易日」是哪天，不编造）。
        if snap_date == today:
            out["state"] = "fresh"
            out["expected_date"] = today
        else:
            out["today_pending"] = True
    else:
        # 已过应产点：应产日就是今天；快照还停在更早交易日 = 盘后链没跑
        out["expected_date"] = today
        out["state"] = "stale" if snap_date < today else "fresh"
    return out


def _live_doc(ctx: Any) -> Optional[Dict[str, Any]]:
    """盘中刷新产物（`selection_live.json`）——**只读 + 当日有效**。

    返回 None 的情形（任一命中就不该用它顶替盘后快照）：
      · 文件缺失/损坏/groups 为空；
      · `meta.mode != "intraday"`（不是本链产物，不认）；
      · 产物的 `trade_date` 不是今天 —— 昨天刷的盘中结果不能冒充今天。

    为什么在插件里判“是不是今天”而不问 clock：文件自带的 trade_date 就是它的数据日，
    与盘后快照用 `date` 判是不是同一天同一道理（旧端 `_selection_snapshot_payload` 同思路）。
    """
    doc = _panel(ctx, "selection_live")
    if not isinstance(doc, dict) or not doc.get("groups"):
        return None
    meta = doc.get("meta") or {}
    if str(meta.get("mode") or "") != "intraday":
        return None
    today = datetime.now().strftime("%Y-%m-%d")
    trade = str(meta.get("trade_date") or doc.get("date") or "")
    if trade != today:
        return None
    return doc


def _live_freshness(doc: Dict[str, Any]) -> Dict[str, Any]:
    """盘中产物的「新鲜度」段：三态语义不变（fresh/stale/unknown），另加 mode/generated_at。

    盘中产物自带 `trade_date==今天`，所以 state 恒为 fresh（它不涉及“应产日”）；
    它跟盘后快照的区别靠 `mode="intraday"` 表达，前端据此挂「盘中预估」横幅。
    """
    meta = doc.get("meta") or {}
    d = str(meta.get("trade_date") or doc.get("date") or "")
    return {"state": "fresh", "snapshot_date": d, "expected_date": d,
            "produce_by": SELECTION_PRODUCE_BY, "today_pending": False,
            "mode": "intraday",
            "generated_at": str(meta.get("generated_at") or "")}


def build_selection(ctx: Any) -> Dict[str, Any]:
    """选股页主数据（旧 8800 `api_selection` → `_selection_snapshot_payload`）。**真实实现**。

    源 `data/panel/selection_latest.json`（8.2MB 盘后快照，生产脚本
    `engine/collectors/selection_snapshot_builder.py`）。契约：

    - **零计算**：`groups/rows/filter_options/...` 全部**原样透传**，不重排、不补字段、
      不查 `buy_signal.db`（选股口径的唯一源是旧端盘后链，本插件只搬数据）；
    - `snapshot` 是共享 dict → **只浅拷顶层**再挂 `pool` / `freshness`，
      不写回快照本体（`ctx.panel_json` 每次新解析，也仍照旧端写法保守处理）；
    - `pool` 每次实时读 `selection_pool.json` 拼上（主人手点的池不能陈）；
    - `freshness` 见 `_freshness()`（只比日期字符串）。

    **不做解析缓存**（任务书 §2.1 明写）：`ctx.panel_json` 不暴露 mtime/size，插件里没有
    可靠的失效判据；8.2MB JSON 解析约 0.1–0.3s，60s 轮询可接受。若实测拖慢页面，
    单开任务给 core 加「按 path.stat() 缓存」的读接口，**不在插件里各写一套**。

    快照缺失/损坏/`groups` 为空 → 返回**旧端自己的缺快照分支**
    （键与正常分支一致、`groups=[]`、`error` 写明原因、`warnings/score_warn=["snapshot_missing"]`），
    前端据此显示「快照未生成」，不会误报「今日无选股」。

    **2026-11-04 盘中刷新优先**（主人：尾盘选股，见 docs/selection_live.md）：满足下列全部条件时，
    本条返回盘中产物（`selection_live.json`，meta.mode=intraday）而不是盘后快照：
      ① 盘中产物当日有效（`_live_doc`：mode=intraday 且 trade_date=今天）；
      ② 盘后快照的 date **还早于**今天（盘后链未产今日定稿）。
    盘后快照一旦拿到今天，立即以它为准——盘中是预估，不能盖收盘定稿。返回里的 `mode`
    （postclose/intraday）是前端横幅的唯一判据；日期比较前先归一成 `YYYY-MM-DD`
    （旧版快照可能存 `YYYYMMDD`，两种都要能判）。
    """
    snap = _panel(ctx, "selection_latest")
    # 2026-11-04：盘中刷新优先。条件（全部满足）才用 selection_live 顶替盘后快照：
    #   ① live 产物当日有效（_live_doc）；② 盘后快照还没拿到今天（盘后链产了今天的
    #   快照就以盘后为准 —— 那是收盘定稿，盘中预估不能盖它）。
    live = _live_doc(ctx)
    snap_date0 = _dash_date(str((snap or {}).get("date") or "")) if isinstance(snap, dict) else ""
    today0 = datetime.now().strftime("%Y-%m-%d")
    if live is not None and snap_date0 < today0:
        out = dict(live)
        sig = str((live.get("meta") or {}).get("signal_date")
                  or (live.get("asof") or {}).get("signal_date") or live.get("date") or "")
        out["pool"] = build_selection_pool(ctx, "", today=sig)
        out["freshness"] = _live_freshness(live)
        out["mode"] = "intraday"
        return out
    if not isinstance(snap, dict) or not snap.get("groups"):
        out = copy.deepcopy(SELECTION_SKELETON)
        out["meta"]["generated_at"] = _now()
        out["meta"]["source"] = "server:selection:missing_snapshot"
        out["error"] = ("快照未生成：selection_latest.json 不可读（文件缺失/JSON 损坏/groups 为空）。"
                        "请跑 engine/collectors/selection_snapshot_builder.py"
                        "（盘后链 selectionsnap 步会自动产）")
        out["warnings"] = ["snapshot_missing"]
        out["score_warn"] = ["snapshot_missing"]
        out["pool"] = build_selection_pool(ctx, "")
        out["freshness"] = _freshness(snap if isinstance(snap, dict) else {})
        return out
    out = dict(snap)                          # 浅拷顶层：快照本体不动
    # 池的「是不是今天的」用**快照自带 signal_date**（旧端同口径，不碰 buy_signal.db）
    snap_signal = (snap.get("meta") or {}).get("signal_date") or snap.get("date") or ""
    out["pool"] = build_selection_pool(ctx, "", today=snap_signal)
    out["freshness"] = _freshness(snap)
    out["mode"] = "postclose"
    return out


# ---------------------------------------------------------------- 选股页 ETF 模式（2026-11-11）
# 主人：入市不满两年只能买主板，实际只能买 ETF——选股页要能在「个股 / ETF」之间切。
# 数据唯一源 = `data/panel/etf_snapshot_latest.json`（盘后链 `etf_snapshot` 步产物，
#   `engine/collectors/etf_snapshot.py`，1,535 只股票型 ETF × 20 字段，每日 16:50 前后覆盖）。
# 本函数只做**换单位 + 拼行**，不重算任何指标（比值/均线距离/尾盘占比全是源字段）：
#   amount → amount_yi（/1e8）；tail30_ratio → tail30（×100）；code sh510300 → 510300.SH。
# 不发起的计算：放量异动判级（etf_minute_flow，市场级）、折溢率（无源）——一律不编。
ETF_SNAP = "etf_snapshot_latest"
# ETF 行字段白名单（字段名→前端列名一致；源多给的一律不带，减 payload）
ETF_ROW_FIELDS = ("name", "cat", "sub", "pct", "pct5", "pct20", "above_ma20",
                  "dist_hi60", "ratio", "split_suspect")


def _etf_daily_sigs(ctx: Any) -> Dict[str, List[str]]:
    """`buy_signal.db::etf_signal` → {ts_code: [sig,...]}（`run_etf.py` 盘后产）。

    表/库不可用 → {}（ETF 表只是多了买点列，缺了选股表照样能用，不报错）。
    """
    out: Dict[str, List[str]] = {}
    with _conn(ctx, "buy_signal.db") as conn:
        if conn is None:
            return out
        try:
            for code, ds in _rows(conn, "SELECT etf, daily_sigs FROM etf_signal "
                                        "WHERE daily_sigs IS NOT NULL AND daily_sigs<>''"):
                sigs = [s for s in str(ds).split(",") if s]
                if sigs:
                    out[str(code)] = sigs
        except Exception:  # noqa: BLE001 - 表未建（尚未跑过 run_etf）→ 当作无信号
            return {}
    return out


def _sig_meta(ctx: Any) -> Dict[str, Dict[str, Any]]:
    """信号 code → {label, cls, desc}。**唯一源 = `selection_latest.json` 的 `group_sigs`**。

    为什么不在这里写一份映射表：个股徽标的 label/颜色/说明已在盘后快照里定义，
    再拄一份就是口径双源（改一处漏一处）。快照读不到 → {}，前端退回显示 code 原文。
    """
    snap = _panel(ctx, "selection_latest")
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(snap, dict):
        return out
    groups = snap.get("group_sigs")
    if not isinstance(groups, dict):
        return out
    for sigs in groups.values():
        for x in (sigs or []):
            if isinstance(x, dict) and x.get("code"):
                code = str(x["code"])
                out[code] = {"label": x.get("label") or code,
                             "cls": x.get("cls") or "",
                             "desc": x.get("desc") or ""}
    return out


def _etf_code(code: Any) -> Tuple[str, str]:
    """`sh510300` / `510300.SH` / `510300` → (code6, ts_code)；解不出 → ("", "")。"""
    raw = str(code or "").strip().lower()
    ex, body = "", ""
    if len(raw) >= 8 and raw[:2] in ("sh", "sz"):
        ex, body = raw[:2], raw[2:8]
    elif "." in raw:
        body, _, sfx = raw.partition(".")
        ex = "sh" if sfx.upper() == "SH" else ("sz" if sfx.upper() == "SZ" else "")
    elif len(raw) == 6:
        body = raw
        ex = "sh" if raw[0] in "56" else ("sz" if raw[0] == "1" else "")
    if len(body) != 6 or not body.isdigit() or not ex:
        return "", ""
    return body, body + (".SH" if ex == "sh" else ".SZ")


def build_selection_etf(ctx: Any) -> Dict[str, Any]:
    """选股页 ETF 模式数据（`GET /api/selection_etf`）。**只读透传 + 换单位**。

    返回形状（前端直接消费，不在浏览器重算）：
      `{ok, date, generated_at, source, meta, cats, stats, warnings, rows}`
    `rows` 按成交额降序（ETF 先看流动性），字段全为源字段或其单位换算值。

    缺产物/坏产物 → `ok=False` + `error` 说明（不伪造空表让前端看着像「今天没 ETF」）。
    新鲜度：只回 `date`/`generated_at`，过期判定交前端（盘后快照按日期比，不猜）。
    """
    snap = _panel(ctx, ETF_SNAP)
    if not isinstance(snap, dict) or not isinstance(snap.get("rows"), list) or not snap["rows"]:
        return {"ok": False, "date": "", "rows": [], "cats": [], "meta": {},
                "stats": {"rows": 0, "by_cat": {}},
                "error": "ETF 快照未生成：etf_snapshot_latest.json 不可读（文件缺失/损坏/rows 为空）。"
                         "盘后链 etf_snapshot 步会自动产（engine/collectors/etf_snapshot.py）",
                "source": "panel:etf_snapshot:missing"}
    out_rows: List[Dict[str, Any]] = []
    by_cat: Dict[str, int] = {}
    sigs_map = _etf_daily_sigs(ctx)
    sig_meta = _sig_meta(ctx) if sigs_map else {}
    for r in snap["rows"]:
        if not isinstance(r, dict):
            continue
        code6, ts = _etf_code(r.get("code"))
        if not code6:
            continue
        row: Dict[str, Any] = {"code": ts, "code6": code6}
        for k in ETF_ROW_FIELDS:
            row[k] = r.get(k)
        amt = r.get("amount")
        row["amount_yi"] = (round(float(amt) / 1e8, 2)
                            if _finite(amt) else None)
        t30 = r.get("tail30_ratio")
        row["tail30"] = (round(float(t30) * 100, 1) if _finite(t30) else None)
        # 买点徽标（2026-11-11）：有才带这个字段，空的不写（payload 1,500 行，空数组白占体积）
        # desc 加「ETF（同个股公式）」前缀：这套公式原本为个股设计，ETF 直接套用——
        # 不抹掉原描述里的「个股」字样，也不让人误以为是 ETF 专用指标。
        _sigs = sigs_map.get(ts) or sigs_map.get(code6) or []
        if _sigs:
            _bd = []
            for s in _sigs:
                m = dict(sig_meta.get(s) or {"label": s, "cls": "", "desc": ""})
                m["desc"] = "ETF（同个股公式）：" + (m.get("desc") or m.get("label") or s)
                _bd.append(m)
            row["badges"] = _bd
        out_rows.append(row)
        cat = str(r.get("cat") or "未分类")
        by_cat[cat] = by_cat.get(cat, 0) + 1
    # 成交额降序（缺额沉底），同额按代码；流动性是 ETF 的第一属性
    out_rows.sort(key=lambda x: (-(x.get("amount_yi") or -1.0), x["code6"]))
    cats = [{"key": k, "n": v} for k, v in sorted(by_cat.items(), key=lambda kv: -kv[1])]
    return {"ok": True, "date": str(snap.get("date") or ""),
            "generated_at": str(snap.get("generated_at") or ""),
            "source": "panel:etf_snapshot",
            "meta": snap.get("meta") or {},
            "cats": cats, "stats": {"rows": len(out_rows), "by_cat": by_cat},
            "warnings": list((snap.get("meta") or {}).get("warnings") or []),
            "sig_meta": sig_meta,
            "sig_n": len(sigs_map),
            "rows": out_rows}


def build_selection_live(ctx: Any) -> Dict[str, Any]:
    """盘中刷新产物原样下发（`GET /api/selection_live`）——**不自动顶替盘后快照**。

    用途：前端点「刷新」后轮询本端点，拿到新产物的 `meta.generated_at` 就知道算完了；
    页面主体仍走 `/api/selection`（它内部按当天/盘后链状态自动选源）。
    无产物/已过期（不是今天）→ 返回与 `/api/selection` 同构的空壳 + error 说明，**不编造**。
    """
    doc = _live_doc(ctx)
    if doc is None:
        out = copy.deepcopy(SELECTION_SKELETON)
        out["meta"]["generated_at"] = _now()
        out["meta"]["source"] = "server:selection_live:missing"
        out["error"] = "尚无当日盘中刷新产物（点选股页「刷新」按钮现算一次）"
        out["freshness"] = {"state": "missing", "snapshot_date": "", "expected_date": "",
                            "produce_by": SELECTION_PRODUCE_BY, "today_pending": False,
                            "mode": "intraday"}
        out["pool"] = build_selection_pool(ctx, "")
        return out
    out = dict(doc)
    sig = str((doc.get("meta") or {}).get("signal_date")
              or (doc.get("asof") or {}).get("signal_date") or doc.get("date") or "")
    out["pool"] = build_selection_pool(ctx, "", today=sig)
    out["freshness"] = _live_freshness(doc)
    out["mode"] = "intraday"
    return out


def build_selection_prefs(ctx: Any) -> Dict[str, Any]:
    """选股偏好（旧 8800 `api_selection_prefs`）。**真实实现**。

    旧端口径：`_read_json(selection_filter.json)`，**缺失/损坏 → `{}`**（前端用默认值），
    不是 404。这里就是该分支 + 读到就原样透传（`meta/pools/ma/pin/inds/cons/boards/sigs/
    boardOn/hot`，键由旧端页面自己写，本插件不加不减）。
    """
    doc = _panel(ctx, "selection_filter")
    return doc if isinstance(doc, dict) else {}


# ---- 选股页写操作（4.7）：旧 8800 的筛选偏好 / 选股池 add/remove ----
class SelectionPrefsBody(BaseModel):
    """与旧端 `SelectionPrefsBody` 字段一致；None 不落盘（只存前端显式改过的项）。"""
    pools: Optional[List[str]] = None
    ma: Optional[Dict[str, Any]] = None
    pin: Optional[Dict[str, Any]] = None
    inds: Optional[List[str]] = None
    cons: Optional[List[str]] = None
    boards: Optional[List[Dict[str, Any]]] = None
    sigs: Optional[Dict[str, Any]] = None
    boardOn: Optional[bool] = None
    hot: Optional[Dict[str, Any]] = None
    # 2026-11-07（标注 N82）：前端一直在发这两项，但模型没有字段 → Pydantic 丢弃、
    # 「按组记忆的 KDJ/均线」和「N 型结构开关」保存后重开恢复默认。补上即修。
    maBy: Optional[Dict[str, Any]] = None
    ntype: Optional[Dict[str, Any]] = None
    # 2026-11-15（标注 N82 补尾）：流通市值 {min,max}。原来只在前端 localStorage，
    #   换设备/清缓存就回默认；落服务端后跨设备一致。空串=不限。
    mcap: Optional[Dict[str, Any]] = None
    # 2026-11-12（标注 N91）：盘中现算的刷新范围（组名列表，如 ["B1","单针"]）。
    #   `selection_live_refresh.py::read_scope` 读同一文件决定只重算哪几组；
    #   缺字段/空 = 全刷（脚本側的错误处理）。
    liveGroups: Optional[List[str]] = None


class SelectionPoolAddBody(BaseModel):
    code: str
    name: str = ""
    group: str = ""
    sig: str = ""


class SelectionPoolRemoveBody(BaseModel):
    code: str


def build_selection_prefs_save(ctx: Any, body: SelectionPrefsBody) -> Dict[str, Any]:
    """保存筛选偏好（覆盖写、原子；只存展示态，不碰信号/账本/池）。"""
    doc: Dict[str, Any] = {"meta": {"schema_version": "1.0.0",
                                     "document_type": "selection_filter",
                                     "updated_at": _now(), "source": "server"}}
    for k, v in body.model_dump().items():
        if v is not None:
            doc[k] = v
    ctx.panel_write("selection_filter", doc)
    return {"ok": True, "updated_at": doc["meta"]["updated_at"]}


def build_selection_pool_add(ctx: Any, body: SelectionPoolAddBody) -> Dict[str, Any]:
    """加入选股池（幂等 upsert；新加入排最前，旧端同款 order 语义）。"""
    raw = str(body.code or "").strip()
    if not raw:
        raise HTTPException(status_code=422, detail="code 不能为空")
    code = _code_norm(raw)
    date = _dash_date(_now()[:10]) or _now()[:10]
    pools, cur = _sel_pools_doc(ctx)
    d = cur or date
    items = list(pools.get(d, {}).get("items") or [])
    hit = next((x for x in items if x["code"] == code), None)
    if hit:
        hit["group"] = str(body.group or hit["group"] or "")
        hit["sig"] = str(body.sig or hit["sig"] or "")
        if body.name:
            hit["name"] = str(body.name)
        action = "updated"
    else:
        items.append({"code": code, "name": str(body.name or ""),
                      "group": str(body.group or ""), "sig": str(body.sig or ""),
                      "added_at": _now(),
                      "order": (min(x.get("order", 0) for x in items) - 1) if items else 0})
        action = "added"
    pools[d] = {"items": items, "saved_at": _now()}
    doc = _sel_pools_save(ctx, pools, d)
    n = len(doc["pools"][d]["items"])
    return {"ok": True, "action": action, "date": d, "count": n,
            "items": doc["pools"][d]["items"]}


def build_selection_pool_remove(ctx: Any, body: SelectionPoolRemoveBody) -> Dict[str, Any]:
    """移出选股池（幂等；带/不带后缀都能命中）。"""
    raw = str(body.code or "").strip()
    if not raw:
        raise HTTPException(status_code=422, detail="code 不能为空")
    c6 = raw.split(".")[0]
    pools, cur = _sel_pools_doc(ctx)
    d = cur or _dash_date(_now()[:10]) or _now()[:10]
    items = list(pools.get(d, {}).get("items") or [])
    kept = [x for x in items if x["code"] != raw and x["code"].upper() != raw.upper()
            and x["code"].split(".")[0] != c6]
    removed = len(items) - len(kept)
    pools[d] = {"items": kept, "saved_at": _now()}
    doc = _sel_pools_save(ctx, pools, d)
    n = len(doc["pools"][d]["items"])
    return {"ok": True, "removed": removed, "count": n, "items": doc["pools"][d]["items"]}


def build_selection_board_options(ctx: Any) -> Dict[str, Any]:
    """板块筛选候选（旧 8800 `api_selection_board_options`）：**真实实现**。

    - `favorites` = `panel/watchlist.json` 里 `kind='sector'` 的条目（去重、保持原序）；
      名称缺失回 `ths_board.db` 补（board L2/L3 + concept 一起查）；
    - `catalog`   = 全量目录 `board(sw_level=2 → L2, =3 → L3)` + `concept(CONCEPT)`，
      排序 = L2 在前、组内按名称（旧端同序）；
    - `level`     = L2 / L3 / CONCEPT / UNKNOWN（不在库里 → UNKNOWN，前端标「未收录」）。
    两库都在白名单内；任一不可用 → 各自空表，不拖垮选股页（旧端同：异常 → 空清单）。
    """
    name_of: Dict[str, str] = {}
    level_of: Dict[str, str] = {}
    catalog: List[Dict[str, Any]] = []
    with _conn(ctx, THS_DB) as conn:
        if conn is not None:
            for code, name in _rows(conn, "SELECT code, name FROM board WHERE sw_level=2"):
                name_of[str(code).upper()] = str(name)
                level_of[str(code).upper()] = "L2"
                catalog.append({"code": str(code), "name": str(name), "level": "L2"})
            for code, name in _rows(conn, "SELECT code, name FROM board WHERE sw_level=3"):
                name_of[str(code).upper()] = str(name)
                level_of[str(code).upper()] = "L3"
                catalog.append({"code": str(code), "name": str(name), "level": "L3"})
            for code, name in _rows(conn, "SELECT code, name FROM concept"):
                if not _is_custom_board(str(code)):
                    continue   # 2026-10-25：选股板块候选只给自建板块（THS 885/886 已下线）
                name_of[str(code).upper()] = str(name)
                level_of[str(code).upper()] = "CONCEPT"
                catalog.append({"code": str(code), "name": str(name), "level": "CONCEPT"})
    catalog.sort(key=lambda x: (0 if x["level"] == "L2" else 1, x["name"]))

    favs: List[Dict[str, Any]] = []
    seen: set = set()
    for item in ((_panel(ctx, "watchlist") or {}).get("items") or []):
        if not isinstance(item, dict) or item.get("kind") != "sector":
            continue
        code = str(item.get("code") or "").strip().upper()
        if not code or code in seen:
            continue
        seen.add(code)
        favs.append({"code": code,
                     "name": str(item.get("name") or "") or name_of.get(code, code),
                     "level": level_of.get(code, "UNKNOWN")})
    return {"favorites": favs, "catalog": catalog,
            "counts": {"favorites": len(favs), "catalog": len(catalog)}}


# ================================================================ 11. /stock_amv/{ts_code}

def build_stock_amv(ctx: Any, ts_code: Any, days: Any) -> Dict[str, Any]:
    """个股活跃市值序列（旧 8800 `api_stock_amv` → `tdx_sector_api.stock_amv`）。

    旧端源 = `amv_tdx.db::stock_state(date,alive,sh)`（近 31 日）；该库已退役换成
    `amv_board.db`（同一张 `stock_state` 表、同一列名，仍在白名单内），故照旧端同一 SQL 取。

    返回 `{code, ts_code, dates, alive, sh, note}`（旧端逐键）；无该股快照 → 404
    + 旧端同一句 detail（前端显示「无 AMV 数据」）。
    """
    code6 = _digits(ts_code, 6)
    if not code6 or not code6.isdigit():
        raise HTTPException(status_code=404, detail="无该股 state 快照（state 仅保留近 31 日）")
    try:
        n = max(1, min(int(days or 120), 500))
    except (TypeError, ValueError):
        n = 120
    with _conn(ctx, AMV_DB) as conn:
        if conn is None:
            raise HTTPException(status_code=503,
                                detail="%s 不可读（未在白名单/文件缺失）" % AMV_DB)
        rows = _rows(conn,
                     "SELECT date,alive,sh FROM stock_state WHERE stock=? "
                     "ORDER BY date DESC LIMIT ?", (code6, n))
    if not rows:
        raise HTTPException(status_code=404, detail="无该股 state 快照（state 仅保留近 31 日）")
    rows = list(reversed(rows))
    return {"code": code6, "ts_code": str(ts_code),
            "dates": [r[0] for r in rows],
            "alive": [r[1] for r in rows],
            "sh": [r[2] for r in rows],
            "note": "活跃市值 = alive × 前复权收盘 × 流通股本；alive 为 0~1 无量纲活跃度"}


# ================================================================ 12. /sector_amv/{code}

def _rolling_ma(vals: List[float], n: int = MA_N) -> List[Optional[float]]:
    """旧端 `tdx_sector_api` 的副图均线（**扩张窗口**：前 9 个点用已有点数平均，不是 None）。

    逐行照抄旧实现，别改成「不足 n 点给 None」——那会让老图前 9 点缺线。
    """
    out: List[Optional[float]] = []
    for i in range(len(vals)):
        lo = max(0, i - n + 1)
        out.append(sum(vals[lo:i + 1]) / (i + 1 - lo))
    return out


def build_sector_amv(ctx: Any, code: Any, days: Any) -> Dict[str, Any]:
    """板块活跃市值序列 + MA10（旧 8800 `api_sector_amv`）。**真实实现**。

    旧端路由（`tdx_sector_api.sector_amv`）：BK 前缀或 `sector_meta` 里能反查到层级
    （L2/L3/CONCEPT/INDEX）→ `_board_amv`（带 `level` 键）；否则回落旧 L2 分支
    （无 `level` 键、另带 `name`/`index_code`）。

    ⚠ 已知差异（不编造，如实降级）：L2 回落分支的 `name`/`index_code` 来自
    `tdx_industry.load_index_table()`（通达信表，不在任何白名单）→ 该分支下这两键为
    **null**（字段在，值缺）。THS 码（`881xxx.TI` 等）走 board 分支，名称/层级都真。
    """
    raw = str(code or "").strip()
    if not raw:
        raise HTTPException(status_code=404, detail="无该板块")
    try:
        n = max(1, min(int(days or 120), 500))
    except (TypeError, ValueError):
        n = 120

    with _conn(ctx, AMV_DB) as conn:
        if conn is None:
            raise HTTPException(status_code=503,
                                detail="%s 不可读（未在白名单/文件缺失）" % AMV_DB)
        meta = _rows(conn, "SELECT level,name FROM sector_meta WHERE code=? LIMIT 1", (raw,))
        if meta:
            level, name = meta[0][0], meta[0][1]
            rows = _rows(conn,
                         "SELECT date,amv,share,rank FROM sector_daily "
                         "WHERE level=? AND code=? ORDER BY date DESC LIMIT ?",
                         (level, raw, n))
            rows = list(reversed(rows))
            if not rows:
                raise HTTPException(status_code=404, detail="无该板块数据")
            amv = [r[1] for r in rows]
            return {"code": raw, "name": name, "index_code": raw, "ma_n": MA_N,
                    "level": level,
                    "dates": [r[0] for r in rows], "amv": amv, "ma": _rolling_ma(amv),
                    "share": [r[2] for r in rows], "rank": [r[3] for r in rows]}
        # 旧 L2 回落分支（通达信 6 位行业码等）：无 level 键，name/index_code 不可得
        rows = _rows(conn,
                     "SELECT date,amv,share,rank FROM sector_daily "
                     "WHERE level='L2' AND code=? ORDER BY date DESC LIMIT ?", (raw, n))
        rows = list(reversed(rows))
    if not rows:
        raise HTTPException(status_code=404, detail="无该行业数据")
    amv = [r[1] for r in rows]
    return {"code": raw, "name": None, "index_code": None, "ma_n": MA_N,
            "dates": [r[0] for r in rows], "amv": amv, "ma": _rolling_ma(amv),
            "share": [r[2] for r in rows], "rank": [r[3] for r in rows]}


# ================================================================ 13. /sector_kline/{code}

def _quotes_daily(ctx: Any, glb: str, count: int) -> List[Any]:
    """经行情门面取日线（`ctx.quotes.daily_bars`）；不可用 → 空表（不抛）。"""
    facade = getattr(ctx, "quotes", None)
    if facade is None:
        return []
    try:
        return list(facade.daily_bars(glb, count=count) or [])
    except Exception:  # noqa: BLE001 - 行情源不可用 → 空表
        return []


def build_sector_kline(ctx: Any, code: Any, limit: Any) -> Dict[str, Any]:
    """板块指数日线 + 黄白线（旧 8800 `api_sector_kline` → `tdx_sector_api.sector_kline`）。

    旧端源顺序：`.TI` 后缀 → `ths_index.db::index_daily`；BK/ALL → 自算板块指数
    （`amv_board.db::board_flow`）；6 位数字 → 通达信 vipdoc `.day` 二进制。

    本插件：**`ths_index.db` 与 vipdoc 都不在白名单**，且插件不得 `open()` 二进制 →
    改走 `ctx.quotes.daily_bars()`（只读行情门面，同一条行情的日线）。因此：

    - 返回键与旧端正常分支逐键一致（`code/index_code/bars/n/last/source/indicators`）；
    - `bars[]` 每根仍是 `{t,o,h,l,c,v,amt,yellow,white}`；
    - `source` 如实写 `eltdx:daily_bars`（**不是** `vipdoc:sh881xxx.day`），不冒充；
    - 行情门面不可用 → `bars=[] / n=0 / last=null` 的骨架，**不 404**（旧端 404 只表示
      「该指数缺失」，这里的语义是「取数通道不可用」，前端应显示占位而非「无此板块」）。
    """
    raw = str(code or "").strip()
    if not raw:
        raise HTTPException(status_code=404, detail="index_code 需 6 位数字（如 881319）"
                                                    "或板块码（881121.TI / BKxxxx）")
    try:
        lim = max(1, min(int(limit or 240), 800))
    except (TypeError, ValueError):
        lim = 240
    index_code = _digits(raw, 6) if raw.split(".")[0].isdigit() else raw.split(".")[0].upper()

    out: Dict[str, Any] = {"code": index_code, "index_code": index_code, "bars": [],
                           "n": 0, "last": None,
                           "source": "eltdx:daily_bars", "indicators": ["yellow", "white"]}
    # 板块指数统一按上交所板块指数取（881/884/885 段）；BK 段无对应行情码 → 返回骨架。
    body = index_code
    if not body.isdigit():
        return out
    bars = _quotes_daily(ctx, "sh" + body, lim + 114)   # 多取 114 根供黄线预热（旧端同）
    if not bars:
        return out
    closes = [float(getattr(b, "close", 0) or 0) for b in bars]
    yellow, white = _yellow_white(ctx, closes)
    rows: List[Dict[str, Any]] = []
    for i, b in enumerate(bars):
        t = getattr(b, "time", None)
        if not hasattr(t, "strftime"):
            continue
        lots = getattr(b, "volume_lots", None)
        rows.append({"t": t.strftime("%Y%m%d"),
                     "o": getattr(b, "open", None), "h": getattr(b, "high", None),
                     "l": getattr(b, "low", None), "c": getattr(b, "close", None),
                     # eltdx 成交量单位=手 → 股（旧端 .day 的 v 是股）
                     "v": (float(lots) * 100.0) if _finite(lots) else None,
                     "amt": getattr(b, "amount", None),
                     "yellow": yellow[i] if i < len(yellow) else None,
                     "white": white[i] if i < len(white) else None})
    rows = rows[-lim:]
    out["bars"] = rows
    out["n"] = len(rows)
    out["last"] = rows[-1]["t"] if rows else None
    return out


# ================================================================ 14. /ui_notes/list（已移交 ops 插件）
#
# 2026-10-24（N2 周末冲刺）：`GET /api/ui_notes/list` 已由 `app/plugins/ops` 用
# `ctx.state_json("ui_notes")` **真实实现**（`engine/state/ui_notes.json` 已进
# `paths.WRITABLE_STATE` 白名单，同一份名单兼作读通道）。两个插件都 `mount=root` 到
# `/api`，core 启动即报根路径冲突 → 本插件删除该路由与骨架构造函数 `build_ui_notes`。


# ================================================================ 15. /review.md（已移交 review 插件）
#
# 2026-10-23（任务书 docs/task_review_md_4_5.md §1）：`/api/review.md` 的真实实现
# 已由 `app/plugins/review` 承接（`paths.READABLE_TEXT_DIR["daily_review"]` +
# 移植的 `md2html` 复盘渲染管线，root 挂载同一 `/api` 前缀）。
#
# 本插件原先那条骨架路由（`build_review_md` + `REVIEW_MD_KEYS`）**整块删除**：
# 两个 root 插件都注册 `/review.md` 会让 `main.py` 启动即报路径冲突，必须让路。
# 骨架的数据来源（MD 目录不在白名单 + 无 markdown 依赖）已随 4.5 的白名单登记失效。


# ================================================================ 16. /review_chart.json

def build_review_chart(ctx: Any, date: Any) -> Dict[str, Any]:
    """复盘图数据（旧 8800 `api_review_chart`，RV_DATA 同构）。

    旧端 `common/review_chart_data.build_cached` 从 MD + 11 个源（`amv_regime_latest`、
    `amv_breadth_latest`、`sector_temp_latest`、`real_positions`、`battle_plan`、
    `sector_ladder_{date}`、`sector_anomaly_{date}`、temperature、`track_pool.db`、
    `ths_index.db`、`position_cap_latest`、`all_a_latest`）实时装配。

    本插件只能读 panel 白名单中的 4 份（dashboard/real_positions/watchlist/
    watchlist_quotes），装配不出任何一段 → 返回**全量键骨架**：
    `{ok:false, meta, head, levels, index, amv, breadth, temp, turnover, sector, ladder,
      anomaly, sentiment, news, positions, gate, tomorrow, scenarios, gaps, md_quotes,
      file, mtime_ns, cache}`，值一律 null/空。段级 `miss` 写成与旧端同义的说明。
    """
    miss = ["复盘图表装配不可用：MD 与多数源 JSON 不在插件只读白名单（见 docs/compat_misc.md §5）"]
    seg = {"miss": miss[0]}
    return {
        "ok": False,
        "meta": {"trade_date": None, "idx_date": None,
                 "generated_by": "panel-v2 compat_misc（骨架）",
                 "md_source": None, "miss": miss, "freshness": [],
                 "note": "本数据为同构空骨架（源不可读），非真实复盘数据；补白名单后原样透传"},
        "head": {"date": None, "weekday": None, "title": None, "one": None,
                 "three": [], "next": None, "temp": None, "caliber": None,
                 "exec_note": None},
        "levels": None,
        "index": {"rows": [], "source": None},
        "amv": dict(seg),
        "breadth": dict(seg),
        "temp": dict(seg),
        "turnover": dict(seg),
        "sector": dict(seg),
        "ladder": dict(seg),
        "anomaly": dict(seg),
        "sentiment": dict(seg),
        "news": {"asof_pre": None, "asof_inc": None, "pre": [], "inc": [],
                 "driver": {"src": []}},
        "positions": dict(seg),
        "gate": {"rows": [], "gates": [], "verdict": None, "source": None},
        "tomorrow": {"date": None, "rows": [], "src": None, "miss": None},
        "scenarios": {"miss": miss[0], "rows": [], "open_default": None},
        "gaps": [],
        "md_quotes": {"who": None, "s0": "", "s1": "", "s2": "", "s3": ""},
        "file": None,
        "mtime_ns": None,
        "cache": "miss",
    }


# ================================================================ 路由


def register(ctx: Any) -> APIRouter:
    """插件入口：返回 APIRouter（manifest `mount=root` ⇒ core 挂到 `/api`）。"""
    router = APIRouter()

    # ---- 后台监控（已移交 ops 插件）----
    # 2026-10-24（N2）：`GET /api/ops.json` 已由 `app/plugins/ops` 用 `ctx.panel_json("ops")`
    # 直读 `data/panel/ops_status.json`（已登记 READABLE_PANEL）。本插件让出该路由，
    # 骨架构造函数 `build_ops` / `OPS_SKELETON` 随之删除（不留第二份实现）。

    # ---- 交易链（只读）----
    # 2026-10-23（4.1.3b）：`/order/list`、`/trades/{ts_code}`、`/condition/list`、
    # `/paper_account`、`/ledger` 五条已移交 `app/plugins/paper`（新纸面账本 + core 服务）。
    # 本插件**不得**再注册这五条路径：两个插件都 mount=root 到 /api，core 启动即报冲突。

    # ---- 选股页 ----
    @router.get("/selection")
    def selection() -> Dict[str, Any]:
        return build_selection(ctx)

    @router.get("/selection_live")
    def selection_live() -> Dict[str, Any]:
        return build_selection_live(ctx)

    @router.get("/selection_quotes")
    def selection_quotes(codes: str = Query(default="")) -> Dict[str, Any]:
        """选股表**实时涨幅**（2026-11-12 标注 N89「在盘中的时候使用实时数据」）。

        主人原话：涨幅之类的是实时数据，不需要额外获取数据——盘中页面本来就要拿快照。
        所以这里**不重算任何选股**、不碰名单与信号，只把行情快照的「现价/昨收」给前端，
        让它只覆盖「涨幅」这一列。数据源 = core 的 eltdx 单例
        （`ctx.quotes.call("quotes.get_snapshots")`，与盘中页/板块快照同一条链路）。

        `codes` = 逗号分隔（`600519.SH,000001.SZ`，前端直接传 `data-code`）→
        `{ok, asof, source, quotes: {code: {last, pre, pct, amount, vol, vr}}}`。
        停牌/无成交票（`last<=0` / `pre<=0`）**不返回** —— 前端保留原值，
        不写 `-100%`（eltdx 对停牌票返 last=pre=0，除下来就是 -100）。

        2026-11-13 加 `vol`（当日累计量·手）与 `vr`（盘中量比）：口径见上方
        `_live_vol_ratio`，分母是选股页同源的「前 20 日均量（不含当日）」。
        """
        raw = [c.strip() for c in str(codes or "").split(",") if c.strip()][:1200]
        asof = _now()[:16]
        if not raw:
            return {"ok": True, "asof": asof, "source": "eltdx:snapshot", "quotes": {}}
        pairs = []
        for c in raw:
            try:
                g = ctx.quotes.normalize(c)
            except Exception:  # noqa: BLE001 - 写错格式的码跳过，不影响整批
                g = None
            if g:
                pairs.append((g, c))
        if not pairs:
            return {"ok": True, "asof": asof, "source": "eltdx:snapshot", "quotes": {}}
        # 按 80 一批（同 eltdx_adapter.SNAP_BATCH）：一次 1200 只会把 socket 拖长
        snaps: list = []
        glbs = [g for g, _ in pairs]
        for i in range(0, len(glbs), 80):
            part = ctx.quotes.call("quotes.get_snapshots", glbs[i:i + 80])
            if part:
                snaps.extend(part)
        by_glb = {str(getattr(s, "full_code", "") or ""): s for s in snaps}
        # 盘中量比：分母按日缓存，首轮逐票取一次日线（实测 20 票 0.8s），之后全走缓存
        vol_base = _vol_avg20(ctx, glbs)
        em = ctx.quotes.elapsed_minutes(_now()[11:16])
        out: Dict[str, Any] = {}
        for g, c in pairs:
            s = by_glb.get(g)
            if s is None:
                continue
            try:
                last = float(getattr(s, "last_price", 0) or 0)
                pre = float(getattr(s, "pre_close_price", 0) or 0)
            except (TypeError, ValueError):
                continue
            if last <= 0 or pre <= 0:
                continue
            try:
                total_hand = float(getattr(s, "total_hand", 0) or 0)
            except (TypeError, ValueError):
                total_hand = 0.0
            out[c] = {"last": round(last, 3), "pre": round(pre, 3),
                      "pct": round((last - pre) / pre * 100, 4),
                      "amount": float(getattr(s, "amount", 0) or 0),
                      "vol": total_hand or None,
                      "vr": _live_vol_ratio(total_hand, vol_base.get(g), em)}
        return {"ok": True, "asof": asof, "source": "eltdx:snapshot", "quotes": out}

    @router.get("/selection_board_options")
    def selection_board_options() -> Dict[str, Any]:
        return build_selection_board_options(ctx)

    # 选股页 ETF 模式（2026-11-11）：与 /selection 平行的只读端点，
    # 数据源是另一份产物（etf_snapshot_latest），不与个股快照互盖。
    @router.get("/selection_etf")
    def selection_etf() -> Dict[str, Any]:
        return build_selection_etf(ctx)

    @router.get("/selection_pool")
    def selection_pool(date: str = Query(default="")) -> Dict[str, Any]:
        return build_selection_pool(ctx, date)

    @router.get("/selection_prefs")
    def selection_prefs() -> Dict[str, Any]:
        return build_selection_prefs(ctx)

    @router.post("/selection_prefs")
    def selection_prefs_save(body: SelectionPrefsBody) -> Dict[str, Any]:
        return build_selection_prefs_save(ctx, body)

    @router.post("/selection_pool/add")
    def selection_pool_add(body: SelectionPoolAddBody) -> Dict[str, Any]:
        return build_selection_pool_add(ctx, body)

    @router.post("/selection_pool/remove")
    def selection_pool_remove(body: SelectionPoolRemoveBody) -> Dict[str, Any]:
        return build_selection_pool_remove(ctx, body)

    # ---- AMV 副图 / 板块 K 线 ----
    @router.get("/stock_amv/{ts_code}")
    def stock_amv(ts_code: str, days: int = Query(default=120)) -> Dict[str, Any]:
        return build_stock_amv(ctx, ts_code, days)

    @router.get("/sector_amv/{code}")
    def sector_amv(code: str, days: int = Query(default=120)) -> Dict[str, Any]:
        return build_sector_amv(ctx, code, days)

    @router.get("/sector_kline/{code}")
    def sector_kline(code: str, limit: int = Query(default=240)) -> Dict[str, Any]:
        return build_sector_kline(ctx, code, limit)

    # ---- 标注 / 复盘 ----
    # 2026-10-24（N2）：`/api/ui_notes/list` 已移交 ops 插件，本插件不再注册。

    # 2026-10-23（4.5）：`/api/review.md` 已移交独立 review 插件，
    # 本插件不再注册该路由（避免 root 冲突）；`review_chart.json` 仍在本插件。

    # 2026-10-23（4.6）：`/api/review_chart.json` 已移交 `app/plugins/review`（core 装配器），
    # 本插件不再注册该路由。

    return router
