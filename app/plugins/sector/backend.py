"""sector 插件后端：板块树 / 某日板块榜 / 单板块 AMV 序列（只读）。

契约：docs/CONTRACT.md；字段映射：docs/sector_contract.md。
数据只经 ctx.db_read(...) 取得只读连接，本文件不出现任何直连数据库调用。
"""

from __future__ import annotations

import contextlib
import os
import time
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence

from fastapi import APIRouter, HTTPException, Query

AMV_DB = "amv_board.db"
POOL_DB = "ths_board.db"
SIG_DB = "buy_signal.db"
MINUTE_DB = "amv_minute.db"
NAMES_JSON = "stock_names"
SCORES_LIB = "panel_scores"   # 评分/名次口径唯一源（CONTRACT §8.1），禁在本文件重写公式
REGIME_LIB = "amv_regime"     # 多空状态机口径唯一源（板块 bull / 个股四态），同上禁重写
CONTRIB_LIB = "amv_contrib"   # AMV 五档分解口径唯一源（/contrib），同上禁重写
SCORE_TRAIL_DAYS = 30         # 旧 8002 score_trail 默认 30 日，前端色格图同窗
# 评分相关进程内缓存（性能红线：3876 成分 full=1 < 3s）。
# 键含 (成员指纹, ds, period) ⇒ 板块/周期/任一新交易日（ds 前进）都自动换键 = 天然失效；
# 成分调整同理改变成员指纹（_members_key）。库上限 32/40 条防膨胀（旧端同值）。
_SCORES_CACHE: Dict[str, Dict[str, Dict[str, Any]]] = {}
_SEC_RANK_CACHE: Dict[tuple, Dict[str, Dict[str, Any]]] = {}
# 板块层快照缓存（2026-10-25 主人报「8810 切层级比 8002 卡」）。
#
# 病根：`/rank_recent` 与 `/board_flow` 每次请求都全量重算（L3 层 board_flow 实测 1.7s，
# 其中 _dual_bull 跑 231 个板块 × 500 日状态机）。8002 首屏一次性拿齐后切层级 0 请求，
# 所以它快。这里加一层进程内缓存，键里带**最新交易日**：
#   · ds 前进（收盘后新数据落库）⇒ 键变 ⇒ 自然失效，不需要定时器；
#   · 同一 ds 内切层级/周期/日期来回跳 ⇒ 命中。
# 值是不可变 dict，命中时直接返回引用（只读使用，不就地改）。
_RECENT_CACHE: Dict[tuple, Dict[str, Any]] = {}
_FLOW_CACHE: Dict[tuple, Dict[str, Any]] = {}
_STRUCT_CACHE: Dict[tuple, Dict[str, Any]] = {}
_CACHE_MAX = 64          # 每类上限；板块层 × 5 层 × 若干周期，64 足够且不膨胀

# 库戳（2026-11-07 夏天核对）：16:10–16:21 链正在重写 amv_board.db，
# 而缓存键只有 (ds, period, level) —— 半成品会被整晚复用。
# 键里加库 mtime+size，库一更新键就变；1 秒节流避免每请求都 stat。
_STAMP: Dict[str, Any] = {"t": 0.0, "v": ""}


def _db_stamp(conn: Any) -> str:
    now = time.monotonic()
    if now - float(_STAMP["t"]) < 1.0:
        return str(_STAMP["v"])
    v = ""
    try:
        rows = conn.execute("PRAGMA database_list").fetchall()
        p = str(rows[0][2]) if rows and len(rows[0]) >= 3 else ""
        if p:
            st = os.stat(p)
            v = "%d:%d" % (st.st_mtime_ns, st.st_size)
    except Exception:  # noqa: BLE001 - 拿不到就不带版本（退回旧行为）
        v = ""
    _STAMP.update(t=now, v=v)
    return v
DUAL_WIN = 500  # 旧 8002 `DUAL_WIN`：双轨状态机取数窗口（交易日）
STOCK_STATE_WIN = 45  # 旧 8002 `_alive_window(days=45)`：个股四态取数窗口

SCALE_S = 1_000_000  # weight / contrib：×1e6 整数
SCALE_A = 10         # 亿元：×10 整数
MA_N = 10            # 与旧 8002 一致
DEFAULT_DAYS = 60
MAX_DAYS = 250
LEVELS = ("L1", "L2", "L3", "CONCEPT", "INDEX")
PERIODS = (1, 3, 5, 10, 20)

# 语义榜条数：候选池的 10%，夹 [10, 30]（旧 8002 `_top_k`，2026-10-02 主人定）
TOP_PCT = 0.10
TOP_MIN, TOP_MAX = 10, 30
TOP_N = 10           # 板块内个股榜深度（stock_top 每板块保留数）


# ---------------------------------------------------------------- 基础工具

def _fetch(conn: Any, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    """执行只读查询并返回 dict 列表。

    不依赖连接上的 row_factory（核心可能用 tuple 也可能用 sqlite3.Row），
    统一按 cursor.description 取列名。
    """
    cur = conn.execute(sql, params)
    if cur.description is None:
        return []
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def _amv_int(v_yuan: Optional[float]) -> Optional[int]:
    """元 → 亿元×10（旧 8002 amv_int 同口径）。"""
    if v_yuan is None:
        return None
    return int(round(v_yuan / 1e8 * SCALE_A))


def _weight_int(v: Optional[float]) -> Optional[int]:
    """share → ×1e6（旧 8002 weight_int 同口径）。"""
    if v is None:
        return None
    return int(round(v * SCALE_S))


def _pct(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if not a or not b:
        return None
    return round((a / b - 1) * 100, 2)


# 全A 行情唯一源（2026-11-01 主人定）：通达信 880008 = 全A等权。
# 与实时卡（AMV_CODE_ALIAS 880008.SH→ALL）、复盘指数卡、state_tree 形态 K 线、盘中卡恐慌图同源。
ALL_A_GLB = "sh880008"


def _apply_all_real_pct(ctx: Any, conn: Any, fl: Dict[str, Any], ds: str, period: int) -> None:
    """就地把 `fl['real_pct']['ALL']`（沪深全A）换成通达信 880008（全A等权）的区间涨跌。

    为何改（2026-11-01 主人对过同花顺）：board_flow 的 ALL 行来自**自算板块指数**
    （vipdoc 流通股本加权·不复权），2026-10-30 当日 −1.83%；同花顺「全A」与通达信 880008
    是**等权**，同日 −3.03%，差 1.2pp。主人要求全A 用 8800XX 口径，只改 ALL 这一行，
    其余板块仍是 THS 真实板块指数（两边不会串口径）。

    窗口口径不变：ds 与 period 个交易日前基准日（与 `_board_flow` 的 lo 同一条窗口），只换数据源。
    取不到（quotes 未授权 / 该日无日线 / 异常）→ 保留原自算值，不置空、不编造。
    """
    if "ALL" not in (fl.get("real_pct") or {}):
        return
    q = getattr(ctx, "quotes", None)
    if q is None:
        return
    dates = _window_dates(conn, ds, max(2, period + 1))    # 降序
    if len(dates) < 2:
        return
    d_s, d_lo = str(ds).replace("-", ""), str(dates[-1]).replace("-", "")
    try:
        bars = q.daily_bars(ALL_A_GLB, 500)    # core 侧有 60s 缓存；500 根 ≈ 2 年，盖住页面可选历史日
    except Exception:      # noqa: BLE001 —— 单行取数失败不拖垮整个榜单
        return
    closes: Dict[str, float] = {}
    for b in bars or []:
        t, c = getattr(b, "time", None), getattr(b, "close", None)
        if t is not None and c is not None:
            closes[t.strftime("%Y%m%d")] = float(c)
    v = _pct(closes.get(d_s), closes.get(d_lo))
    if v is None:
        return
    fl["real_pct"]["ALL"] = v
    a = (fl.get("amv_pct") or {}).get("ALL")
    if a is not None:
        fl["diverge"]["ALL"] = round(a - v, 2)


def _norm_level(level: Optional[str], default: str = "L2") -> str:
    lv = (level or default).strip().upper()
    if lv not in LEVELS:
        raise HTTPException(status_code=400, detail="level 必须是 %s 之一" % "/".join(LEVELS))
    return lv


def _norm_period(period: Any) -> int:
    try:
        p = int(period)
    except (TypeError, ValueError):
        return 1
    return p if p in PERIODS else 1


def _db(ctx: Any, name: str) -> Any:
    """取只读连接；核心未提供或库不可用 → 503（不静默降级成空数据）。"""
    getter = getattr(ctx, "db_read", None)
    if not callable(getter):
        raise HTTPException(status_code=500, detail="ctx.db_read 不可用")
    try:
        return getter(name)
    except Exception as exc:  # noqa: BLE001 - 库缺失/权限问题统一暴露为 503
        raise HTTPException(status_code=503, detail="%s 不可读: %s" % (name, exc))


@contextlib.contextmanager
def _db_conn(ctx: Any, name: str) -> Iterator[Any]:
    """只读连接上下文：ctx.db_read 每次调用新建连接，用完必须关，否则句柄泄漏。"""
    conn = _db(ctx, name)
    try:
        yield conn
    finally:
        with contextlib.suppress(Exception):
            conn.close()


# ---------------------------------------------------------------- 取数

def _resolve_date(conn: Any, ds: Optional[str]) -> Optional[str]:
    if ds:
        return ds
    rows = _fetch(conn, "SELECT MAX(date) AS d FROM daily_total")
    return rows[0]["d"] if rows else None


def _window_dates(conn: Any, ds: str, n: int) -> List[str]:
    """截至 ds（含）的最近 n 个交易日，降序。"""
    return [r["date"] for r in _fetch(
        conn, "SELECT date FROM daily_total WHERE date<=? ORDER BY date DESC LIMIT ?", (ds, n))]


def _meta_map(conn: Any, level: str) -> Dict[str, Dict[str, Any]]:
    return {m["code"]: m for m in _fetch(
        conn, "SELECT * FROM sector_meta WHERE level=? ORDER BY ord", (level,))}


def _resolve_level(conn: Any, code: str, level: Optional[str]) -> tuple:
    """level 缺省时按 sector_meta 反查（多命中取首个）；与旧 8002 resolve_level 一致。"""
    if level:
        lv = level.strip().upper()
        if lv == "L1" and not code.startswith("SW:"):
            code = "SW:" + code
        return _norm_level(lv), code
    if code.startswith("SW:"):
        return "L1", code
    rows = _fetch(conn, "SELECT level FROM sector_meta WHERE code=? LIMIT 2", (code,))
    return (rows[0]["level"] if rows else "L2"), code


def _pool_members(ctx: Any) -> Dict[str, Optional[int]]:
    """分类池成分数 {code: n_members}（board + concept + broad_index，同旧 8002 pool_meta）。

    池库是可选增强（只补 n_members）；不可用 → 空表，不拖垮调用方。
    """
    out: Dict[str, Optional[int]] = {}
    try:
        cm = _db_conn(ctx, POOL_DB)
        conn_pool = cm.__enter__()
    except Exception:  # noqa: BLE001 - 池库不可用 = 只丢 n_members，不拖垮调用方
        return out
    try:
        for sql in ("SELECT code, n_members FROM board",
                    "SELECT code, n_members FROM concept",
                    "SELECT code, n_members FROM broad_index"):
            try:
                for r in _fetch(conn_pool, sql):
                    out[r["code"]] = r["n_members"]
            except Exception:  # noqa: BLE001 - 表缺失 = 该源无数据，不阻断
                continue
    finally:
        with contextlib.suppress(Exception):
            cm.__exit__(None, None, None)
    return out


def _board_flow(conn: Any, ds: str, period: int, level: Optional[str],
                allowed: Optional[Iterable[str]]) -> Dict[str, Any]:
    """板块资金/真实涨跌/活跃市值涨跌/背离（旧 8002 ep_board_flow 的单日单层切片）。

    口径（与旧 8002 逐值一致）：
      flow_calc = Σflow_calc(元)/1e8 亿元，窗口 [flow_lo, ds]
      flow_main = Σflow_main(万元)，同一窗口
      real_pct  = close[ds]/close[lo]-1，lo 为 period 个交易日前的基准日
      amv_pct   = amv[ds]/amv[lo]-1
      diverge   = amv_pct - real_pct

    level 必须传入：`BK9999` 同时存在于 L2 与 L3 且 AMV 完全不同（实测 80.3 亿 vs
    20488 亿），不过滤会跨层覆盖取错值。
    """
    dates = _window_dates(conn, ds, period + 1)
    if not dates:
        return {"flow_calc": {}, "flow_main": {}, "real_pct": {}, "amv_pct": {},
                "diverge": {}, "from_date": None}
    lo = min(dates)
    flow_lo = dates[-2] if len(dates) > 1 else ds

    allow = None if allowed is None else set(allowed)

    flow_calc: Dict[str, Optional[float]] = {}
    flow_main: Dict[str, Optional[float]] = {}
    for r in _fetch(conn,
                    "SELECT board_code, SUM(flow_calc) AS fc, SUM(flow_main) AS fm FROM board_flow "
                    "WHERE date>=? AND date<=? GROUP BY board_code", (flow_lo, ds)):
        code = r["board_code"]
        if allow is not None and code not in allow:
            continue
        fc, fm = r["fc"], r["fm"]
        flow_calc[code] = None if fc is None else round(fc / 1e8, 2)
        flow_main[code] = None if fm is None else round(fm, 1)

    close_lo = {r["board_code"]: r["close"] for r in _fetch(
        conn, "SELECT board_code, close FROM board_flow WHERE date=?", (lo,))}
    close_ds = {r["board_code"]: r["close"] for r in _fetch(
        conn, "SELECT board_code, close FROM board_flow WHERE date=?", (ds,))}

    amv_ds = {r["code"]: r["amv"] for r in _fetch(
        conn, "SELECT code, amv FROM sector_daily WHERE date=?" + (" AND level=?" if level else ""),
        (ds, level) if level else (ds,))}
    amv_lo = {r["code"]: r["amv"] for r in _fetch(
        conn, "SELECT code, amv FROM sector_daily WHERE date=?" + (" AND level=?" if level else ""),
        (lo, level) if level else (lo,))}

    if allow is None:
        real_pct = {c: _pct(close_ds.get(c), close_lo.get(c)) for c in close_ds}
        amv_pct = {c: _pct(a, amv_lo.get(c)) for c, a in amv_ds.items()}
    else:
        real_pct = {c: _pct(close_ds.get(c), close_lo.get(c)) for c in allow if c in close_ds}
        amv_pct = {c: _pct(amv_ds[c], amv_lo.get(c)) for c in allow if c in amv_ds}
    real_pct = {k: v for k, v in real_pct.items() if v is not None}
    amv_pct = {k: v for k, v in amv_pct.items() if v is not None}

    diverge: Dict[str, float] = {}
    for c in set(amv_pct) | set(real_pct):
        a, r = amv_pct.get(c), real_pct.get(c)
        if a is not None and r is not None:
            diverge[c] = round(a - r, 2)

    return {"flow_calc": flow_calc, "flow_main": flow_main,
            "real_pct": real_pct, "amv_pct": amv_pct, "diverge": diverge,
            "from_date": lo}


STRUCT_LIB = "amv_struct_tag"   # 结构标签口径唯一源（量价齐升/纯换手虚火…），禁重写
CONTRIB_LIB = "amv_contrib"      # 五档分解口径唯一源，禁重写


def _struct_tags(ctx: Any, ds: str, level: Optional[str], codes: Iterable[str],
                 period: int = 1) -> Dict[str, Dict[str, Any]]:
    """各板块「结构标签」→ {code: {tag,src,label,dir,dom,px,act,sh,den,total}}。

    口径唯一源：
      · 五档分解 = `amv_contrib.board_parts`（逐日链式，恒等于 ΔAMV）
      · 标签判定 = `amv_struct_tag.compute_row`
    本函数只负责「取数」，零公式。

    **周期必须与表格一致**（主人 2026-10-27）：表格选的 N 日，徽标也必须算 N 日。
    早先固定取「最后两个交易日」→ 选 3 日时表格显示 3 日涨跌、徽标却是单日，口径分裂。
    现：基准日 = ds 往前 period 个交易日（与 _board_flow 的 lo 同源）。

    **覆盖限制**：分项需要 `stock_state`（alive/sh），该表库内仅近 31 个交易日
    （2026-09-07 起）。基准日超出该范围 → 返回 {}，前端显示「—」，**不编造**。
    缓存键带 (ds, level, period, 板块数)。
    """
    codes = set(codes)
    if not codes:
        return {}
    lib = ctx.lib(STRUCT_LIB)
    with _db_conn(ctx, AMV_DB) as conn:
        # 键带库戳：16:10–16:21 链重写库时，半成品不得被整晚复用（2026-11-07）
        ck = ("struct", ds, level, period, len(codes), _db_stamp(conn))
        hit = _STRUCT_CACHE.get(ck)
        if hit is not None:
            return hit
        # 基准日：ds 及其前 period 个交易日（与 amv_pct / real_pct 同口径）
        dates = _window_dates(conn, ds, period + 1)   # 降序
        if not dates or len(dates) < 2:
            _STRUCT_CACHE[ck] = {}
            return {}
        d1 = dates[0]
        d0 = dates[-1]
        if d1 != ds:
            _STRUCT_CACHE[ck] = {}
            return {}
        # AMV：板块自己的活跃市值两端值
        amv0 = {r["code"]: r["amv"] for r in _fetch(
            conn, "SELECT code, amv FROM sector_daily WHERE date=?" + (" AND level=?" if level else ""),
            (d0, level) if level else (d0,))}
        amv1 = {r["code"]: r["amv"] for r in _fetch(
            conn, "SELECT code, amv FROM sector_daily WHERE date=?" + (" AND level=?" if level else ""),
            (d1, level) if level else (d1,))}
        # 价格：板块指数收盘（与表内「真实涨跌」同源）
        close0 = {r["board_code"]: r["close"] for r in _fetch(
            conn, "SELECT board_code, close FROM board_flow WHERE date=?", (d0,))}
        close1 = {r["board_code"]: r["close"] for r in _fetch(
            conn, "SELECT board_code, close FROM board_flow WHERE date=?", (d1,))}

    out: Dict[str, Dict[str, Any]] = {}
    for code in codes:
        a0, a1 = amv0.get(code), amv1.get(code)
        c0, c1 = close0.get(code), close1.get(code)
        amv_pct = ((a1 / a0 - 1) * 100) if (a0 and a1 and a0 > 0) else None
        px_pct = ((c1 / c0 - 1) * 100) if (c0 and c1 and c0 > 0) else None
        if amv_pct is None and px_pct is None:
            continue
        try:
            out[code] = lib.compute_row(amv_pct, px_pct)
        except Exception:  # noqa: BLE001
            continue
    _STRUCT_CACHE[ck] = out
    return out


# ---------------------------------------------------------------- 端点实现

def _node(m: Dict[str, Any], members: Dict[str, Optional[int]]) -> Dict[str, Any]:
    """sector_meta 行 → 目录节点（与旧 8002 node() 同形状）。"""
    return {"code": m["code"], "name": m["name"], "level": m["level"], "l1": m.get("l1"),
            "level_num": m.get("level_num"), "exclude": bool(m.get("exclude")),
            "is_unmap": bool(m.get("is_unmap")), "n_members": members.get(m["code"])}


def build_tree(ctx: Any) -> Dict[str, Any]:
    """GET /tree ← 旧 8002 GET /api/sector_tree（api_catalog 别名）。"""
    members = _pool_members(ctx)
    with _db_conn(ctx, AMV_DB) as conn:
        l1 = [_node(m, members) for m in _fetch(conn, "SELECT * FROM sector_meta WHERE level='L1' ORDER BY ord")]
        l2 = [_node(m, members) for m in _fetch(conn, "SELECT * FROM sector_meta WHERE level='L2' ORDER BY ord")]
        l3 = [_node(m, members) for m in _fetch(conn, "SELECT * FROM sector_meta WHERE level='L3' ORDER BY ord")]
        cc = [_node(m, members) for m in _fetch(conn, "SELECT * FROM sector_meta WHERE level='CONCEPT' ORDER BY ord")]
        idx = [_node(m, members) for m in _fetch(conn, "SELECT * FROM sector_meta WHERE level='INDEX' ORDER BY ord")]
    tree = []
    for m1 in l1:
        kids = [x for x in l2 if x["l1"] == m1["name"]]
        tree.append(dict(m1, children=kids))
    return {"levels": list(LEVELS), "l1": l1, "l2": l2, "l3": l3, "concept": cc, "index": idx,
            "tree": tree,
            "counts": {"L1": len(l1), "L2": len(l2), "L3": len(l3),
                       "CONCEPT": len(cc), "INDEX": len(idx)},
            "note": "行业互斥（Σ==大盘）；概念板块/指数非互斥（一股可属多板块/多指数）"}


def build_search(ctx: Any, q: str, limit: int) -> Dict[str, Any]:
    """GET /search：名称/代码子串，完全相等 → 前缀 → 子串。"""
    kw = (q or "").strip()
    if not kw:
        return {"q": kw, "n": 0, "items": []}
    cat = build_tree(ctx)
    pool = cat["l1"] + cat["l2"] + cat["l3"] + cat["concept"]
    k = kw.lower()
    hits = [x for x in pool if k in (x.get("name") or "").lower() or k in (x.get("code") or "").lower()]
    by_name: Dict[str, Dict[str, Any]] = {}
    for x in hits:
        name = x.get("name")
        if name not in by_name or (x.get("level_num") or 0) > (by_name[name].get("level_num") or 0):
            by_name[name] = x
    hits = list(by_name.values())

    def score(x: Dict[str, Any]) -> tuple:
        name = (x.get("name") or "").lower()
        return (0 if name == k else 1 if name.startswith(k) else 2,
                x.get("level_num") or 9, len(x.get("name") or ""))

    hits.sort(key=score)
    return {"q": kw, "n": len(hits), "items": hits[:limit]}


def _member_codes(ctx: Any, level: str, code: str) -> List[str]:
    """从 ths_board.db 取成分代码；缺表/缺库返回空表（不拖垮调用方）。"""
    table, col = {
        "L2": ("board_member", "board_code"),
        "L3": ("board_member", "board_code"),
        "CONCEPT": ("concept_member", "concept_code"),
        "INDEX": ("broad_index_member", "index_code"),
    }.get(level, (None, None))
    if not table:
        return []
    try:
        with _db_conn(ctx, POOL_DB) as conn:
            return [r["stock"] for r in _fetch(conn, "SELECT stock FROM %s WHERE %s=?" % (table, col), (code,))]
    except Exception:  # noqa: BLE001 - 池库不可用 = 无成分
        return []


def _stock_names(ctx: Any, ds: str) -> Dict[str, str]:
    """代码→名称。主源 = eltdx 全量表（`data/stock_names.json`，经 `ctx.json_read("stock_names")`）；

    回退顺序：全量表 → track_pool.pool（18 个关注票）→ stock_top 当日名称。
    旧 8002 只读全量表，缺失时回显代码；本实现多两层回退，但同名冲突时全量表优先。
    """
    out: Dict[str, str] = {}
    getter = getattr(ctx, "json_read", None)
    if callable(getter):
        try:
            payload = getter(NAMES_JSON)
            names = payload.get("names") if isinstance(payload, dict) else None
            if isinstance(names, dict):
                out.update({str(k): str(v) for k, v in names.items() if v})
        except Exception:  # noqa: BLE001 - 名称表缺失 = 仅少名称，不拖垮接口
            pass
    try:
        with _db_conn(ctx, "track_pool.db") as conn:
            for r in _fetch(conn, "SELECT ts_code, name FROM pool"):
                out.setdefault(r["ts_code"], r["name"])
                out.setdefault(str(r["ts_code"]).split(".")[0], r["name"])
    except Exception:  # noqa: BLE001
        pass
    try:
        with _db_conn(ctx, AMV_DB) as conn:
            for r in _fetch(conn, "SELECT stock_code, stock_name FROM stock_top WHERE date=?", (ds,)):
                out.setdefault(r["stock_code"], r["stock_name"])
    except Exception:  # noqa: BLE001
        pass
    return out


def _signal_map(ctx: Any) -> Dict[str, Dict[str, Any]]:
    """买点徽标：{stock6: {'sigs': [...], 'date': 'YYYYMMDD'}}（旧 8002 `sig_map()` 同口径）。

    daily_sigs 与 weekly_sigs 合并（日信号在前），date 取两者较大值（旧端不暴露日期，
    新版额外给出，便于前端判新鲜度）。信号库不可用 → 空表，不拖垮接口。
    """
    out: Dict[str, Dict[str, Any]] = {}
    try:
        with _db_conn(ctx, SIG_DB) as conn:
            rows = _fetch(conn, "SELECT stock, daily_date, daily_sigs, weekly_date, weekly_sigs "
                                "FROM stock_signal")
    except Exception:  # noqa: BLE001 - 信号库缺失 = 无徽标
        return out
    for r in rows:
        daily = [s for s in str(r.get("daily_sigs") or "").split(",") if s]
        weekly = [s for s in str(r.get("weekly_sigs") or "").split(",") if s]
        if not daily and not weekly:
            continue
        dates = [str(d) for d in (r.get("daily_date"), r.get("weekly_date")) if d]
        out[str(r["stock"]).split(".")[0]] = {
            "sigs": daily + weekly,
            "date": max(dates) if dates else None,
        }
    return out


def _guess_ts_code(code: str) -> str:
    """6 位股票代码 → 交易所后缀（仅用于 pool 缺名时的查询兜底）。

    2026-10-22 修：920xxx 归北交所（旧 8002 `_stock_closes` 同款判定）。此前首字符 '9'
    被归沪市 → 920xxx 的 real_pct 恒为 None（实测 920087/920403 共 121 处差异的根因）。
    """
    c = str(code)
    if c[:3] == "920" or c[:1] in ("4", "8"):
        return c + ".BJ"
    if c[:1] in ("6", "5", "9"):
        return c + ".SH"
    return c + ".SZ"


def _kline_closes(ctx: Any, ds: str, base: str, codes: List[str]) -> Dict[str, Dict[str, float]]:
    """stock 6 位 -> {YYYYMMDD: close}；只查给定成分，走 ts_code 主键索引。"""
    try:
        with _db_conn(ctx, "track_pool.db") as conn:
            ts_map = {str(r["ts_code"]).split(".")[0]: r["ts_code"]
                      for r in _fetch(conn, "SELECT ts_code FROM pool")}
            want = [ts_map.get(c) or _guess_ts_code(c) for c in codes]
            rows = []
            for i in range(0, len(want), 400):
                chunk = want[i:i + 400]
                marks = ",".join("?" for _ in chunk)
                rows.extend(_fetch(
                    conn,
                    "SELECT ts_code, trade_date, close FROM kline WHERE ts_code IN (%s) AND trade_date IN (?,?)" % marks,
                    tuple(chunk) + (ds.replace("-", ""), base.replace("-", "")),
                ))
    except Exception:  # noqa: BLE001
        return {}
    out: Dict[str, Dict[str, float]] = {}
    for r in rows:
        if r.get("close") is None:
            continue
        key = str(r["ts_code"]).split(".")[0]
        out.setdefault(key, {})[str(r["trade_date"])] = float(r["close"])
    return out


def _stock_bull_map(ctx: Any, ds: str, codes: List[str]) -> Dict[str, Optional[str]]:
    """个股四态多空徽标 → {code: '多'/'偏多'/'空'/'偏空'/None}。

    口径唯一源 = `amv_regime.stock_state`（经 `ctx.lib` 加载，CONTRACT §8.1）。
    取数：`stock_amv` 近 45 日逐日 AMV（= alive × 不复权收盘，与旧端 `_stock_bull` 同口径）。
    旧端用 `_alive_window(45)` 的 alive × `_stock_closes` 现算；本实现直接读落盘 AMV，
    同一口径不同取数路径（已与旧端逐值比对）。
    """
    if not codes:
        return {}
    lib = ctx.lib(REGIME_LIB)
    with _db_conn(ctx, AMV_DB) as conn:
        window = list(reversed(_window_dates(conn, ds, STOCK_STATE_WIN)))
        if not window:
            return {}
        series: Dict[str, List[float]] = {}
        for i in range(0, len(codes), 400):
            chunk = codes[i:i + 400]
            marks = ",".join("?" for _ in chunk)
            for r in _fetch(conn, "SELECT date, stock, amv FROM stock_amv WHERE stock IN (%s) "
                                  "AND date>=? AND date<=? ORDER BY date" % marks,
                            tuple(chunk) + (window[0], ds)):
                if r["amv"] is None:
                    continue
                series.setdefault(str(r["stock"]), []).append(float(r["amv"]))
    out: Dict[str, Optional[str]] = {}
    for code in codes:
        vals = series.get(code) or []
        if len(vals) < 12:
            out[code] = None
            continue
        try:
            out[code] = lib.stock_state(vals)
        except Exception:  # noqa: BLE001 - 单票算失败不影响整表
            out[code] = None
    return out


def _build_members_full(ctx: Any, code: str, date: Optional[str], period: int, limit: int) -> Dict[str, Any]:
    """全量成分股现算（旧 8002 `api_members2` 的只读等价）。

    口径（与旧 8002 逐值一致，已比对 period=1/3/5）：
      amt      = round(amv[ds] / 1e8 × 10)            单位「亿元×10」
      amt_avg  = round(窗口内逐日 amv 均值 / 1e8 × 10)   窗口 = period+1 日
      contrib  = round(amv[ds] / total[ds] × 1e6)      权重（前端 /1e4 = %）
      d_contrib= contrib[ds] − contrib[base]            单位 pp
      real_pct = close[ds]/close[base]-1（track_pool.db::kline）
      amv_pct  = amv[ds]/amv[base]-1
      main_flow= Σ stock_flow.main_flow，窗口 [flow_lo, ds]（旧端 flow_lo=dates[-2]，
                 即 period=1 时仅当日，period=5 时 4 日；不是 base）
      sigs     = 买点徽标（buy_signal.db 的 daily_sigs + weekly_sigs 合并，与旧 sig_map() 同）
    """
    with _db_conn(ctx, AMV_DB) as conn:
        lv, code = _resolve_level(conn, code, None)
        ds = _resolve_date(conn, date)
        if not ds:
            raise HTTPException(status_code=404, detail="no data")
        meta = _fetch(conn, "SELECT name FROM sector_meta WHERE level=? AND code=?", (lv, code))
        sector_name = meta[0]["name"] if meta else None
        totals = {r["date"]: r["total"] for r in _fetch(
            conn, "SELECT date, total FROM daily_total WHERE date<=? ORDER BY date DESC LIMIT ?",
            (ds, period + 1))}
        total = totals.get(ds)
        dates = sorted(totals)                     # 升序，末位 = ds
        base = dates[0] if dates else ds
        # 旧 8002 的 flow_lo = 降序列表的 [-2]，等价于升序列表的 [1]（period=1 时即 ds 本身）
        flow_lo = dates[1] if len(dates) > 1 else ds
        base_total = totals.get(base)
        flow = {}
        try:
            for r in _fetch(conn, "SELECT stock_code, SUM(main_flow) AS v FROM stock_flow "
                                  "WHERE date>=? AND date<=? GROUP BY stock_code", (flow_lo, ds)):
                flow[r["stock_code"]] = None if r["v"] is None else round(r["v"], 1)
        except Exception:  # noqa: BLE001 - 表缺失 = 无主力数据
            flow = {}
    codes = _member_codes(ctx, lv, code)
    if not codes and not str(code).endswith(".TI"):
        # 2026-10-25 口径（夏天）：自建板块（999 段）在 sector_meta / concept_member 中存带
        # .TI 后缀的码；裸码调用（手输 URL / 外部脚本）level 反查落空 → 默认 L2 → 查不到成分 404。
        # 补后缀重试一次（带后缀主路径不变；响应 code 字段仍返回调用方原样）。
        with _db_conn(ctx, AMV_DB) as conn:
            lv2, code2 = _resolve_level(conn, str(code) + ".TI", None)
        if code2 != code:
            retry = _member_codes(ctx, lv2, code2)
            if retry:
                codes, lv = retry, lv2
    if not codes:
        raise HTTPException(status_code=404, detail="no members for %s at %s" % (code, ds))
    names = _stock_names(ctx, ds)
    closes = _kline_closes(ctx, ds, base, codes)
    sigs = _signal_map(ctx)
    # 窗口内逐日 amv（只取成分股，走 (date,stock) 主键）
    series: Dict[str, Dict[str, float]] = {}
    for i in range(0, len(codes), 400):
        chunk = codes[i:i + 400]
        marks = ",".join("?" for _ in chunk)
        with _db_conn(ctx, AMV_DB) as conn:
            for r in _fetch(conn, "SELECT date, stock, amv FROM stock_amv WHERE stock IN (%s) "
                                  "AND date>=? AND date<=?" % marks, tuple(chunk) + (base, ds)):
                if r["amv"] is None:
                    continue
                series.setdefault(r["stock"], {})[r["date"]] = r["amv"]
    rows = []
    for stock in codes:
        per_day = series.get(stock) or {}
        value = per_day.get(ds)
        # 当日无 amv 的票**也要出行**（旧 8002 同此）：它们不占名次头部，但计入 n_total、
        # 影响 rank 的末尾位置。实测 885338.TI 有 8 只（600301/601238/601995...）当日无值，
        # 若 `continue` 掉，行数 3868 vs 旧端 3876、且所有名次尾部错位。
        prev = per_day.get(base)
        cl = closes.get(stock, {})
        real_pct = _pct(cl.get(ds.replace("-", "")), cl.get(base.replace("-", "")))
        amv_pct = _pct(value, prev)
        window = [per_day[d] for d in dates if per_day.get(d) is not None]
        amv_avg = sum(window) / len(window) if window else None
        cur_share = (value / total) if (value is not None and total) else None
        base_share = (prev / base_total) if (prev is not None and base_total) else None
        contrib = _weight_int(cur_share)
        base_contrib = _weight_int(base_share)
        sig = sigs.get(stock) or {}
        rows.append({
            "stock_code": stock,
            "stock_name": names.get(stock) or stock,
            "amv": value,
            "amt": _amv_int(value),
            "amt_avg": _amv_int(amv_avg),
            "contrib": contrib,
            "d_contrib": (contrib - base_contrib)
                         if (contrib is not None and base_contrib is not None) else None,
            "amv_pct": amv_pct,
            "real_pct": real_pct,
            "diverge": round(amv_pct - real_pct, 2) if amv_pct is not None and real_pct is not None else None,
            "main_flow": flow.get(stock),
            "sigs": sig.get("sigs") or [],
            "sigs_date": sig.get("date"),
        })
    rows.sort(key=lambda r: (-(r["amv"] or 0), r["stock_code"]))
    n_total = len(rows)
    for i, row in enumerate(rows, 1):
        row["rank"] = i
    if limit and limit > 0:
        shown = rows[:limit]
    else:
        # 旧 8002：INDEX 层默认只返 60（全 A 5571 只，前端卡顿治理）；其他层默认全量
        shown = rows[:60] if lv == "INDEX" else rows
    # 个股四态多空：只算**返回行**（旧端同为「限返 60 已在此前裁好」再算，全 A 全算会拖慢）
    bulls = _stock_bull_map(ctx, ds, [r["stock_code"] for r in shown])
    # 个股结构标签（2026-10-27 统一口径）：与板块**同一个 classify()**，
    # 逐股跑 amv_contrib.parts() 得 px/act/sh。早先用的「AMV涨幅−真实涨幅」差值法已废。
    stk_st: Dict[str, Dict[str, Any]] = {}
    if base and ds:
        with _db_conn(ctx, AMV_DB) as conn:
            stk_st = _stock_struct(ctx, conn, [r["stock_code"] for r in shown], base, ds, ds)
    for r in shown:
        r["st"] = stk_st.get(r["stock_code"])
    # 评分 + 板块内名次：口径唯一源 = panel_scores（ctx.lib），本文件零公式。
    # 成分全集（rows 未裁剪）算名次，才能与旧端 `sector_rank_trail(codes, ...)` 的分母一致。
    notice = None
    smap: Dict[str, Dict[str, Any]] = {}
    strail: Dict[str, Dict[str, Any]] = {}
    if _scores_lib(ctx) is not None:
        with _db_conn(ctx, AMV_DB) as conn:
            smap = _stock_score_map(ctx, conn, ds)
            strail = _sec_rank_trail(ctx, conn, ds, [r["stock_code"] for r in rows], period)
    else:
        notice = "panel_scores 口径库不可用，score/mkt_rank/sec_rank/trail 已置空"
    for row in shown:
        row["bull"] = bulls.get(row["stock_code"])
        sc = smap.get(row["stock_code"]) or {}
        st = strail.get(row["stock_code"]) or {}
        row["score"] = sc.get("score")
        row["mkt_rank"] = sc.get("mkt_rank")
        row["sec_rank"] = st.get("sec_rank")
        row["trail"] = st.get("trail") or []
        # 旧端 r['sec_total'] = len(codes)：板块成分数（含当日无值票），非返回行数
        row["sec_total"] = len(codes)
    out = {"code": code, "level": lv, "date": ds, "base_date": base, "period": period,
           "sector_name": sector_name, "n": len(shown), "n_total": n_total,
           "source": "full", "full": True, "rows": shown}
    if notice:
        out["notice"] = notice
    return out


def _scores_lib(ctx: Any) -> Optional[Any]:
    """拿评分口径库 facade；不可用返回 None（调用方降级，不 500）。

    结果挂在 ctx 对象上缓存（一个进程一个 ctx）：重复调用不重走 import/AST 校验，
    且换 ctx（如测试换 FakeCtx）自动换缓存盒，不会串。
    """
    box = getattr(ctx, "_panel_scores_lib", None)
    if box is not None:
        return box[0]
    try:
        lib = ctx.lib(SCORES_LIB)
    except Exception:  # noqa: BLE001 - 库缺失 = 评分字段降级为 null，不拖垮接口
        lib = None
    try:
        ctx._panel_scores_lib = (lib,)
    except Exception:  # noqa: BLE001 - ctx 不让挂属性（极端）就不缓存，功能不受影响
        pass
    return lib


def _members_key(codes: List[str]) -> tuple:
    """成员指纹（旧 8002 同式）：前 3 个代码 + 数量，足以区分板块。"""
    return (tuple(sorted(codes))[:3], len(codes))


def _stock_score_map(ctx: Any, conn: Any, ds: str) -> Dict[str, Dict[str, float]]:
    """全市场个股评分 {stock: {score, mkt_rank}}（旧 8002 `stock_score_map` 的只读等价）。

    公式全部在口径库 `panel_scores.score_map`（口径唯一源）；本函数只取数：
    只查实际会用到的日期（当日 + 各窗口基准日），与旧端「不比旧实现多查」一致。
    """
    lib = _scores_lib(ctx)
    if lib is None:
        return {}
    days = [r["date"] for r in _fetch(
        conn, "SELECT date FROM daily_total WHERE date<=? ORDER BY date DESC LIMIT 21", (ds,))]
    if not days:
        return {}
    need = [days[0]] + [days[n] for n, _w in lib.SCORE_W if n < len(days)]
    amv_by_date = {d: {r["stock"]: r["amv"] for r in _fetch(
        conn, "SELECT stock, amv FROM stock_amv WHERE date=?", (d,))} for d in need}
    totals = {r["date"]: r["total"] for r in _fetch(
        conn, "SELECT date, total FROM daily_total WHERE date IN (%s)" % ",".join("?" * len(need)), need)}
    return lib.score_map(days, amv_by_date, totals)


def _sec_rank_trail(ctx: Any, conn: Any, ds: str, codes: List[str], period: int) -> Dict[str, Dict[str, Any]]:
    """板块内名次轨迹（旧 8002 `sector_rank_trail` 的只读等价，缓存策略同旧端）。"""
    lib = _scores_lib(ctx)
    if lib is None:
        return {}
    ck = (_members_key(codes), ds, period)
    if ck in _SEC_RANK_CACHE:
        return _SEC_RANK_CACHE[ck]
    need_days = SCORE_TRAIL_DAYS + period
    dates = [r["date"] for r in _fetch(
        conn, "SELECT date FROM daily_total WHERE date<=? ORDER BY date DESC LIMIT ?", (ds, need_days))]
    if len(dates) < period + 1:
        _SEC_RANK_CACHE[ck] = {}
        return {}
    ph = ",".join("?" * len(dates))
    # v is None 时记 0.0：与旧端 `v or 0.0` 同口径（缺值不剔除，避免名次集合漂移）
    amv: Dict[str, Dict[str, float]] = {}
    for r in _fetch(conn, "SELECT date, stock, amv FROM stock_amv WHERE date IN (%s)" % ph, dates):
        amv.setdefault(r["date"], {})[r["stock"]] = r["amv"] or 0.0
    totals = {r["date"]: r["total"] for r in _fetch(
        conn, "SELECT date, total FROM daily_total WHERE date IN (%s)" % ph, dates)}
    out = lib.rank_trail(dates, amv, totals, list(codes), period, SCORE_TRAIL_DAYS)
    if len(_SEC_RANK_CACHE) > 32:
        _SEC_RANK_CACHE.clear()
    _SEC_RANK_CACHE[ck] = out
    return out


def build_score_trail(ctx: Any, code: str, period: int, days: int) -> Dict[str, Any]:
    """GET /score_trail ← 旧 8002 `api_score_trail`：板块成员个股评分轨迹（色格图用）。

    公式唯一源 = `panel_scores.score_map` + `score_trail`（CONTRACT §8.1）。
    逐日算全市场评分再取本板块成员（旧端同法，成本 ≈60ms/日）；
    库不可用 → ok=False + notice，不抛 500（降级契约）。
    """
    lib = _scores_lib(ctx)
    if lib is None:
        return {"ok": False, "reason": "panel_scores 口径库不可用", "code": code, "period": period}
    with _db_conn(ctx, AMV_DB) as conn:
        lv, code = _resolve_level(conn, code, None)
        ds = _resolve_date(conn, None)
        if not ds:
            return {"ok": False, "reason": "no data", "code": code, "period": period}
        names = _stock_names(ctx, ds)
        dates_desc = [r["date"] for r in _fetch(
            conn, "SELECT date FROM daily_total WHERE date<=? ORDER BY date DESC LIMIT ?", (ds, days))]
        # 全市场逐日评分有日级缓存（_SCORES_CACHE）：同日第二板块近乎免费（旧端同设计）
        score_maps: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for d in dates_desc:
            ck = (d,)
            if ck not in _SCORES_CACHE:
                if len(_SCORES_CACHE) > 40:
                    _SCORES_CACHE.clear()
                _SCORES_CACHE[ck] = _stock_score_map(ctx, conn, d)
            score_maps[d] = _SCORES_CACHE[ck]
    codes = _member_codes(ctx, lv, code)
    if not codes:
        return {"ok": False, "reason": "无成分股", "code": code, "period": period}
    trail = lib.score_trail(list(reversed(dates_desc)), score_maps, codes)
    rows = [{"code": c, "name": names.get(c) or c, "score": (score_maps.get(ds) or {}).get(c, {}).get("score"),
             "mkt_rank": (score_maps.get(ds) or {}).get(c, {}).get("mkt_rank"),
             "trail": trail.get(c) or []} for c in codes]
    return {"date": ds, "sector": code, "period": period, "days": days, "rows": rows}


def build_members(ctx: Any, code: str, date: Optional[str], limit: int,
                  full: bool = False, period: int = 1) -> Dict[str, Any]:
    """GET /members/{code}：默认 stock_top 子集；full=1 走全量只读现算。"""
    if full:
        return _build_members_full(ctx, code, date, period, limit)
    limit = limit if limit and limit > 0 else 200
    with _db_conn(ctx, AMV_DB) as conn:
        ds = _resolve_date(conn, date)
        if not ds:
            raise HTTPException(status_code=404, detail="no data")
        rows = _fetch(
            conn,
            "SELECT date, sector_code, stock_code, stock_name, amt, contrib, rank "
            "FROM stock_top WHERE sector_code=? AND date=? ORDER BY rank LIMIT ?",
            (code, ds, limit),
        )
    if not rows:
        raise HTTPException(status_code=404, detail="no members for %s at %s" % (code, ds))
    return {"code": code, "date": ds, "n": len(rows), "source": "stock_top",
            "full": False, "note": "stock_top TOP-N 子集；full=1 走全量现算",
            "rows": rows}


def build_rank(ctx: Any, date: Optional[str], level: str, period: int) -> Dict[str, Any]:
    """GET /rank ← 旧 8002 GET /api/sector_level（某层当日全表读数）。"""
    with _db_conn(ctx, AMV_DB) as conn:
        ds = _resolve_date(conn, date)
        if not ds:
            return {"error": "no data"}
        rows = _fetch(conn,
                      "SELECT code, name, amv, share, rank FROM sector_daily "
                      "WHERE date=? AND level=? ORDER BY code", (ds, level))
        if not rows:
            return {"date": ds, "level": level, "period": period, "n": 0, "rows": []}
        meta = _meta_map(conn, level)
        fl = _board_flow(conn, ds, period, level, {r["code"] for r in rows})
        _apply_all_real_pct(ctx, conn, fl, ds, period)     # 仅 ALL 存在时生效（INDEX 层）
    out = []
    for r in rows:
        m = meta.get(r["code"], {})
        if m.get("exclude") or m.get("is_unmap"):
            continue
        out.append({"code": r["code"], "name": r["name"], "l1": m.get("l1"),
                    "amv": _amv_int(r["amv"]), "weight": _weight_int(r["share"]),
                    "rank": r["rank"],
                    "amv_pct": fl["amv_pct"].get(r["code"]),
                    "real_pct": fl["real_pct"].get(r["code"]),
                    "diverge": fl["diverge"].get(r["code"])})
    return {"date": ds, "level": level, "period": period, "n": len(out), "rows": out}


def _top_k(n: int) -> int:
    """榜单条数 = 候选池 10%，夹 [TOP_MIN, TOP_MAX]，且不超半数（旧 8002 `_top_k`）。"""
    k = min(TOP_MAX, max(TOP_MIN, int(round(n * TOP_PCT))))
    return max(1, min(k, n // 2))


def _prev_date(conn: Any, ds: str, n: int = 1) -> Optional[str]:
    rows = [r["date"] for r in _fetch(
        conn, "SELECT date FROM daily_total WHERE date<? ORDER BY date DESC LIMIT ?", (ds, n))]
    return rows[-1] if len(rows) == n else None


def _sector_row(conn: Any, ds: str, level: str, code: str) -> Optional[Dict[str, Any]]:
    rows = _fetch(conn, "SELECT * FROM sector_daily WHERE date=? AND level=? AND code=?", (ds, level, code))
    return rows[0] if rows else None


def _top_of_sector(conn: Any, ds: str, sector_code: str, top_n: int = TOP_N) -> List[Dict[str, Any]]:
    """某板块某日个股榜 → [{code,name,amt,contrib,rank}]（旧 8002 top_of_sector 同形）。"""
    return [{"code": r["stock_code"], "name": r["stock_name"],
             "amt": _amv_int(r["amt"]), "contrib": _weight_int(r["contrib"]),
             "rank": r["rank"]}
            for r in _fetch(conn, "SELECT stock_code, stock_name, amt, contrib, rank FROM stock_top "
                                  "WHERE date=? AND sector_code=? ORDER BY rank", (ds, sector_code))][:top_n]


def _build_row(conn: Any, ds: str, level: str, code: str, n_back: int = 1,
               with_top: bool = True, top_n: int = TOP_N) -> Optional[Dict[str, Any]]:
    """逻辑行（旧 8002 build_row 同形）：d_weight = weight[ds] − weight[ds−n_back]。"""
    if level == "L1" and not code.startswith("SW:"):
        code = "SW:" + code
    s = _sector_row(conn, ds, level, code)
    if not s:
        return None
    meta = _meta_map(conn, level).get(code, {})
    d_weight = None
    if n_back:
        pd = _prev_date(conn, ds, n_back)
        if pd:
            ps = _sector_row(conn, pd, level, code)
            if ps:
                d_weight = s["share"] - ps["share"]
    row = {"date": ds, "level": level, "code": code,
           "name": s["name"] or meta.get("name"),
           "amv": _amv_int(s["amv"]), "weight": _weight_int(s["share"]),
           "rank": s["rank"], "d_weight": _weight_int(d_weight), "top": []}
    if with_top and level == "L2" and code != "BK9999":
        row["top"] = _top_of_sector(conn, ds, code, top_n)
    return row


def build_rank_top(ctx: Any, date: Optional[str], level: str, n: int) -> Dict[str, Any]:
    """GET /rank_top ← 旧 8002 `ep_rank`：当日流入/流出 TOP 10%（较 n 日前）+ 新进/退出。

    口径（与旧端逐值一致）：池 = 当日该层有 share 且 n 日前也有 share 的非 exclude 板块；
    按 share 差值降序；k = `_top_k(池大小)`；`top_out` 取尾部 k 个（降序）且不与 top_in 重叠。
    """
    with _db_conn(ctx, AMV_DB) as conn:
        ds = _resolve_date(conn, date)
        if not ds:
            return {"error": "no data"}
        rows = _fetch(conn, "SELECT code, name, share, rank FROM sector_daily WHERE date=? AND level=?",
                      (ds, level))
        meta = _meta_map(conn, level)
        pd = _prev_date(conn, ds, n)
        prev_weight = {r["code"]: r["share"] for r in _fetch(
            conn, "SELECT code, share FROM sector_daily WHERE date=? AND level=?", (pd, level))} if pd else {}
        pool = []
        for r in rows:
            m = meta.get(r["code"], {})
            if m.get("exclude"):
                continue
            if r["share"] is None or r["code"] not in prev_weight:
                continue
            pool.append((r["code"], r["share"] - prev_weight[r["code"]], r["share"]))
        pool.sort(key=lambda x: -x[1])
        k = _top_k(len(pool))
        top_in = pool[:k]
        in_codes = {c for c, _, _ in top_in}
        top_out = [x for x in pool[-k:][::-1] if x[0] not in in_codes]
        prev_top = _top_set(conn, pd, level, n) if pd else set()
        cur_top = {c for c, _, _ in top_in}
        total = _fetch(conn, "SELECT total FROM daily_total WHERE date=?", (ds,))
        return {
            "date": ds, "level": level, "n": n, "prev_date": pd,
            "total": _amv_int(total[0]["total"]) if total else None,
            "top_in": [_build_row(conn, ds, level, c, n_back=n, with_top=(level == "L2"))
                       for c, _, _ in top_in],
            "top_out": [_build_row(conn, ds, level, c, n_back=n, with_top=(level == "L2"))
                        for c, _, _ in top_out],
            "new": sorted(cur_top - prev_top),
            "exit": sorted(prev_top - cur_top),
        }


def _top_set(conn: Any, ds: Optional[str], level: str, n: int) -> set:
    """某日 TOP 集合（旧 8002 `_top_set` 同式）。"""
    if not ds:
        return set()
    rows = _fetch(conn, "SELECT code, share FROM sector_daily WHERE date=? AND level=?", (ds, level))
    meta = _meta_map(conn, level)
    pd = _prev_date(conn, ds, n)
    prev_weight = {r["code"]: r["share"] for r in _fetch(
        conn, "SELECT code, share FROM sector_daily WHERE date=? AND level=?", (pd, level))} if pd else {}
    pool = []
    for r in rows:
        if meta.get(r["code"], {}).get("exclude"):
            continue
        if r["share"] is None or r["code"] not in prev_weight:
            continue
        pool.append((r["code"], r["share"] - prev_weight[r["code"]]))
    pool.sort(key=lambda x: -x[1])
    return {c for c, _ in pool[:_top_k(len(pool))]}


def build_trend(ctx: Any, code: str, days: int) -> Dict[str, Any]:
    """GET /trend ← 旧 8002 `ep_trend`：板块近 N 日 amv + weight + MA10。

    与 `/series` 的差别：`/trend` 只返回序列、不附带 flow/pct 等区间字段，行数可到 250；
    level 按代码前缀推断（`SW:` → L1，否则 L2），与旧端一致。
    """
    level = "L1" if code.startswith("SW:") else "L2"
    with _db_conn(ctx, AMV_DB) as conn:
        rows = _fetch(conn, "SELECT date, amv, share, rank, name FROM sector_daily "
                            "WHERE level=? AND code=? ORDER BY date DESC LIMIT ?", (level, code, days))
        rows.reverse()
        if not rows:
            return {"error": "not found", "code": code}
        amv_yuan = [r["amv"] / 1e8 for r in rows]
        ma = []
        for i in range(len(amv_yuan)):
            seg = amv_yuan[max(0, i - MA_N + 1):i + 1]
            ma.append(sum(seg) / len(seg))
        last = _sector_row(conn, rows[-1]["date"], level, code) or {}
        return {"code": code, "level": level, "name": last.get("name") or rows[-1].get("name"),
                "ma_n": MA_N, "dates": [r["date"] for r in rows],
                "amv": [_amv_int(v * 1e8) for v in amv_yuan],
                "ma": [_amv_int(v * 1e8) for v in ma],
                "weight": [_weight_int(r["share"]) for r in rows],
                "rank": [r["rank"] for r in rows]}


def build_board_top(ctx: Any, sector: str, date: Optional[str], top_n: int) -> Dict[str, Any]:
    """GET /board_top ← 旧 8002 `ep_board_top`：板块内个股 TOP N + 名次变化（较前一日）。"""
    with _db_conn(ctx, AMV_DB) as conn:
        ds = _resolve_date(conn, date)
        if not ds:
            return {"error": "no data"}
        cur = _top_of_sector(conn, ds, sector, top_n)
        pd = _prev_date(conn, ds, 1)
        prev_rank = {r["stock_code"]: r["rank"] for r in _fetch(
            conn, "SELECT stock_code, rank FROM stock_top WHERE date=? AND sector_code=?", (pd, sector))} if pd else {}
        items = []
        for t in cur:
            pr = prev_rank.get(t["code"])
            items.append({"code": t["code"], "name": t["name"], "amt": t["amt"],
                          "contrib": t["contrib"], "rank": t["rank"], "rank_prev": pr,
                          "rank_delta": (pr - t["rank"]) if pr is not None else None})
        meta = _fetch(conn, "SELECT name FROM sector_meta WHERE level='L2' AND code=?", (sector,))
        return {"date": ds, "sector": sector,
                "sector_name": meta[0]["name"] if meta else None,
                "top": top_n, "prev_date": pd, "items": items}


def _dual_bull(ctx: Any, ds: str, level: Optional[str], cur: Iterable[str]) -> Dict[str, bool]:
    """各板块「双轨多空确认态」→ {code: True(多)/False(空)}。

    口径唯一源 = `夏天-activity/工具/amv_regime.py::compute_regime`（经 `ctx.lib` 加载，
    CONTRACT §8.1）。旧 8002 `_dual_bull` 同式：跑窗口内该板块 AMV 全序列
    （含切换前历史以定状态），取末值；窗口内无确认时回退单轨比较（不编造）。
    """
    lib = ctx.lib(REGIME_LIB)
    codes = set(cur)
    if not codes:
        return {}
    with _db_conn(ctx, AMV_DB) as conn:
        dts = _window_dates(conn, ds, DUAL_WIN)
        if not dts:
            return {}
        lo = min(dts)
        sql = "SELECT code, date, amv FROM sector_daily WHERE date>=? AND date<=?"
        params: tuple = (lo, ds)
        if level:
            sql += " AND level=?"
            params = (lo, ds, level)
        sql += " ORDER BY code, date"
        rows = _fetch(conn, sql, params)
    by_code: Dict[str, List[tuple]] = {}
    for r in rows:
        if r["code"] not in codes or r["amv"] is None:
            continue
        entry = by_code.setdefault(r["code"], ([], []))
        entry[0].append(r["date"])
        entry[1].append(float(r["amv"]))
    out: Dict[str, bool] = {}
    for code, (dates, values) in by_code.items():
        if not values:
            continue
        try:
            st = lib.compute_regime(dates, values)["state"][-1]
        except Exception:  # noqa: BLE001 - 单板块算失败不拖垮整层
            st = None
        if st is None:
            k = min(len(values), MA_N)
            out[code] = bool(values[-1] >= sum(values[-k:]) / k)
        else:
            out[code] = (st == "多")
    return out


# ---------------------------------------------------------------- 盘后落盘缓存

# 盘后结果落盘（2026-11-07）：收盘后板块资金榜不再变化，重启/机器重开后首屏不必重算
# （L3 层单次实测 1.7s，其中 `_dual_bull` 1.2s）。路径归 core（`data/panel_v2/_cache/sector/`），
# 本文件不碰文件系统。
#
# 三道铁律（core `ctx.cache_read` 文档）：空结果不写｜失败/降级不写｜读时数据日必须对得上。
# 失效：`doc["version"]` = amv_board.db 的 mtime+size（链重写库即变），`doc["date"]` = 数据日；
# 条目键 = (period, level)，写前读-改-写，别的周期/层级不得被冲掉。
_FLOW_DISK_NAME = "board_flow"


def _is_postclose(ctx: Any) -> bool:
    """收盘后（数据固定）时段。唯一口径 = core `trade_clock.postclose()`
    （非交易日，或交易日 >= 16:40 = 收盘链 READY_DEADLINE）。

    门面缺失/抛错 → False：宁可现算，不拿旧值当固定值。
    """
    try:
        return bool(ctx.trade_clock.postclose())
    except Exception:  # noqa: BLE001 - 未注入/日历故障一律按“未就绪”
        return False


def _flow_disk_key(period: int, level: Optional[str]) -> str:
    """落盘条目键 `p<period>|<层>`；level 缺省（全层）记 ALL。"""
    return "p%s|%s" % (period, (level or "ALL").upper())


def _flow_has_data(out: Any) -> bool:
    """有真实分项才算有数据：空榜/降级/error 结构一律不得落盘（铁律 1、2）。"""
    if not isinstance(out, dict) or "error" in out:
        return False
    return any(isinstance(out.get(k), dict) and bool(out[k])
               for k in ("flow_calc", "amv_pct", "real_pct", "bull"))


def _flow_disk_get(ctx: Any, dk: str, ds: str, stamp: str) -> Optional[Dict[str, Any]]:
    """盘后读盘：库戳与数据日都要对上，条目必须是 dict。任一不符/异常 → None（现算）。"""
    if not stamp:
        return None
    try:
        doc = ctx.cache_read(_FLOW_DISK_NAME)
    except Exception:  # noqa: BLE001 - 首次/未声明/损坏 → 现算
        return None
    if not isinstance(doc, dict) or doc.get("version") != stamp or doc.get("date") != ds:
        return None
    items = doc.get("items")
    if not isinstance(items, dict):
        return None
    hit = items.get(dk)
    return hit if isinstance(hit, dict) else None


def _flow_disk_put(ctx: Any, dk: str, ds: str, stamp: str, out: Dict[str, Any]) -> None:
    """盘后写盘（读-改-写）：同库戳同数据日的其它 period/level 条目原样保留。"""
    if not stamp or not _flow_has_data(out):
        return
    try:
        doc = ctx.cache_read(_FLOW_DISK_NAME)
    except Exception:  # noqa: BLE001 - 首次/损坏 → 新建
        doc = None
    if (not isinstance(doc, dict) or doc.get("version") != stamp
            or doc.get("date") != ds or not isinstance(doc.get("items"), dict)):
        doc = {"version": stamp, "date": ds, "items": {}}
    doc = dict(doc)
    items = dict(doc["items"])
    items[dk] = out
    doc["items"] = items
    try:
        ctx.cache_write(_FLOW_DISK_NAME, doc)
    except Exception:  # noqa: BLE001 - 写失败只是下次多算一次，不影响响应
        pass


def build_board_flow(ctx: Any, date: Optional[str], period: int,
                     level: Optional[str]) -> Dict[str, Any]:
    """GET /board_flow ← 旧 8002 `ep_board_flow`：全层批量资金/真实涨跌/背离/多空。

    `level` 缺省 = 全层（各层 code 不重复，可合并）；传 level = 只该层。
    `bull` 走口径库 `amv_regime`（CONTRACT §8.1），与旧端 `_dual_bull` 同式。

    带进程内缓存（见 `_FLOW_CACHE`）：这个端点最贵——L3 层实测 1.7s，
    其中 `_dual_bull` 跑 231 个板块 × 500 日状态机（约 1.2s）。键 = (ds, period, level)。

    盘后（`postclose`）内存 miss 时再查落盘缓存：重启后首屏直接拿昨晚算好的结果，
    库戳（mtime+size）或数据日一变当 miss ⇒ 盘中/数据更新后行为与原先一致。
    """
    postclose = _is_postclose(ctx)
    with _db_conn(ctx, AMV_DB) as conn:
        ds = _resolve_date(conn, date)
        if not ds:
            return {"error": "no data"}
        stamp = _db_stamp(conn)
        ck = ("bf", ds, period, level, stamp)
        hit = _FLOW_CACHE.get(ck)
        if hit is not None:
            return hit
        dk = _flow_disk_key(period, level)
        if postclose:
            stored = _flow_disk_get(ctx, dk, ds, stamp)
            if stored is not None:
                if len(_FLOW_CACHE) >= _CACHE_MAX:      # 回填内存：同进程后续请求不再读盘
                    _FLOW_CACHE.clear()
                _FLOW_CACHE[ck] = stored
                return stored
        fl = _board_flow(conn, ds, period, level, None)
        _apply_all_real_pct(ctx, conn, fl, ds, period)     # 全A 换 880008 等权（见函数注释）
        allowed = None
        if level:
            allowed = {m["code"] for m in _fetch(
                conn, "SELECT code FROM sector_meta WHERE level=?", (level,))}
        main_days: Dict[str, int] = {}
        dates = _window_dates(conn, ds, period + 1)   # 降序（ORDER BY date DESC）
        # 旧端 flow_lo = 降序列表 [-2]（= 第二老的一天；period=1 时即 ds 自身）
        flow_lo = dates[-2] if len(dates) > 1 else ds
        for r in _fetch(conn, "SELECT board_code, COUNT(flow_main) AS n FROM board_flow "
                              "WHERE date>=? AND date<=? GROUP BY board_code", (flow_lo, ds)):
            main_days[r["board_code"]] = r["n"]
        # 双轨多空（口径库）：取当前层的 code 集合作为候选
        cur_codes = {m["code"] for m in _fetch(
            conn, "SELECT code FROM sector_daily WHERE date=?" + (" AND level=?" if level else ""),
            (ds, level) if level else (ds,))}
    bull = _dual_bull(ctx, ds, level, cur_codes)
    # 结构标签（齐升/齐跌/价驱/量驱/混合）。**周期与表格一致**（主人 2026-10-27）。
    # 需 stock_state（近 31 日）；超出范围返回 {}，前端显示「—」，不编造。
    stg = _struct_tags(ctx, ds, level, cur_codes, period)
    out = {"date": ds, "from_date": fl["from_date"], "period": period, "level": level,
           "n_days": len(dates),
           "unit": "flow_calc=亿元（涨跌成交额差·自算）｜flow_main=万元（主力动向·eltdx）",
           "source": "全部本地自算；板块分类=同花顺 THS（行业+概念，2026-09-30 换源；东财已退役）",
           "flow_calc": fl["flow_calc"], "flow_main": fl["flow_main"],
           "main_days": main_days, "amv_pct": fl["amv_pct"],
           "real_pct": fl["real_pct"], "diverge": fl["diverge"], "bull": bull,
           "st": stg,
           "st_period": period,
           "st_days": 31}
    if allowed is not None:
        for key in ("flow_calc", "flow_main", "main_days", "amv_pct", "real_pct",
                    "diverge", "bull", "st"):
            out[key] = {k: v for k, v in out[key].items() if k in allowed}
    if len(_FLOW_CACHE) >= _CACHE_MAX:
        _FLOW_CACHE.clear()
    _FLOW_CACHE[ck] = out
    if postclose:                       # 只有非空结果才落盘（_flow_disk_put 内再校验）
        _flow_disk_put(ctx, dk, ds, stamp, out)
    return out


def _contrib_struct(ctx: Any, conn: Any, codes: List[str], d0: str, d1: str) -> Dict[str, Any]:
    """板块结构指标：价格区间 / 广度 / top1 贡献 / 有效贡献股数。

    口径（2026-10-27 补，对齐参考站点「资金结构（贡献分解）」）：
      广度          ΔAMV>0 的成分股占比（%）——板块上涨的“宽度”
      top1 贡献    最大单只贡献占全部正向贡献之和的比例（%）——单票拉动程度
      有效贡献股数  1 / Σ(w_i²)，w_i = |ΔAMV_i| / Σ|ΔAMV|（HHI 倒数）——“多少只股票在真出力”
      价格区间      板块成分股的区间涨跌幅（中位）——比 AMV 更接近人看的“价格”

    **窗口与 period 一致**（主人 2026-10-27 要求口径统一）。
    数据源：stock_amv（全成分，2024-09-30 起）+ track_pool.kline（收盘价）。
    """
    if not codes:
        return {}
    try:
        m0, m1 = {}, {}
        for d, s, v in conn.execute(
                "SELECT date, stock, amv FROM stock_amv WHERE date IN (?,?) AND stock IN (%s)"
                % ",".join("?" * len(codes)), tuple([d0, d1] + list(codes))):
            (m0 if d == d0 else m1)[s] = v
        common = [s for s in codes if s in m0 and s in m1]
        if not common:
            return {}
        deltas = [(m1[s] - m0[s]) for s in common]
        tot = sum(deltas)
        pos = sum(1 for x in deltas if x > 0)
        absum = sum(abs(x) for x in deltas)
        out: Dict[str, Any] = {
            "n_calc": len(common),
            "breadth": round(pos / len(common) * 100, 1),
        }
        if absum > 0:
            ws = [abs(x) / absum for x in deltas]
            hhi = sum(w * w for w in ws)
            out["eff_n"] = round(1 / hhi, 1) if hhi else None
            out["top1"] = round(max(abs(x) for x in deltas) / absum * 100, 1)
        return out
    except Exception:  # noqa: BLE001 - 结构指标失败不影响主分项
        return {}


def _stock_struct(ctx: Any, conn: Any, codes: List[str], d0: str, d1: str,
                  ds: str) -> Dict[str, Dict[str, Any]]:
    """逐股结构标签：与板块**同一套口径**——只比两个百分比。

    2026-10-27 主人定：不用五档分项，直接比
        amv_pct（活跃市值涨幅）vs px_pct（价格涨幅）
    两个量同窗口、同单位（%），差额 gap 就是换手贡献（pp）。

    返回 {stock_code: {tag,label,dir,gap,amv_pct,px_pct}}
    """
    if not codes:
        return {}
    lib = ctx.lib(STRUCT_LIB)
    out: Dict[str, Dict[str, Any]] = {}
    try:
        closes = _kline_closes(ctx, ds, d0, list(codes))
        rows = conn.execute(
            "SELECT a.stock AS s, a.amv AS v1, b.amv AS v0 "
            "FROM stock_amv a JOIN stock_amv b ON b.stock=a.stock AND b.date=? "
            "WHERE a.date=? AND a.amv IS NOT NULL AND b.amv IS NOT NULL", (d0, ds)).fetchall()
        want = set(codes)
        for s, v1, v0 in rows:
            if s not in want:
                continue
            amv_pct = ((v1 / v0 - 1) * 100) if (v0 and v0 > 0) else None
            cl = closes.get(s) or {}
            c1 = cl.get(ds.replace("-", ""))
            c0 = cl.get(d0.replace("-", ""))
            px_pct = ((c1 / c0 - 1) * 100) if (c0 and c1 and c0 > 0) else None
            if amv_pct is None and px_pct is None:
                continue
            out[s] = lib.compute_row(amv_pct, px_pct)
    except Exception:  # noqa: BLE001 - 个股标签失败不影响主表
        return {}
    return out


def _contrib_top(ctx: Any, conn: Any, code: str, ds: str, d0: str, d1: str,
                 top_n: int = 5) -> List[Dict[str, Any]]:
    """Top 贡献股：谁在拉这个板块（按 ΔAMV 降序）。

    每只返回：
      delta_pp  该股对板块占比变化的贡献（‰，与 parts 同单位）
      px_pct    该股区间价格涨跌幅（%，来自 track_pool.kline）
      amv_pct   该股区间 AMV 涨跌幅（%）
      driver    驱动类型：量驱 / 价驱（对齐板块徽标口径，逐股版）
                判据：AMV 涨幅与价格涨幅谁大——AMV 远高于价格 → 量驱（换手抬起来的）
                      价格与 AMV 接近       → 价驱（价格抬起来的）
    """
    try:
        T1 = conn.execute("SELECT total FROM daily_total WHERE date=?", (ds,)).fetchone()
        if not T1 or not T1[0]:
            return []
        T1 = float(T1[0])
        rows = conn.execute(
            "SELECT a.stock, a.amv AS v1, b.amv AS v0 FROM stock_amv a "
            "JOIN stock_amv b ON b.stock=a.stock AND b.date=? "
            "WHERE a.date=? AND a.amv IS NOT NULL AND b.amv IS NOT NULL", (d0, ds)).fetchall()
        mem = set(_member_codes(ctx, "L3", code)) or set(_member_codes(ctx, "L2", code))
        cand = [(r[0], r[1] - r[2], r[1], r[2]) for r in rows if r[0] in mem and (r[1] - r[2]) != 0]
        cand.sort(key=lambda x: -x[1])
        cand = cand[:top_n]
        if not cand:
            return []
        names = _stock_names(ctx, ds)
        closes = _kline_closes(ctx, ds, d0, [c[0] for c in cand])
        # 驱动标签走统一口径（2026-10-27）：逐股五档分项 → 同一个 classify()
        st_tags = _stock_struct(ctx, conn, [c[0] for c in cand], d0, ds, ds)
        out = []
        for sc, dv, v1, v0 in cand:
            cl = closes.get(sc) or {}
            c1 = cl.get(ds.replace("-", ""))
            c0 = cl.get(d0.replace("-", ""))
            px_pct = round((c1 / c0 - 1) * 100, 2) if (c1 and c0 and c0 > 0) else None
            amv_pct = round((v1 / v0 - 1) * 100, 2) if v0 else None
            st = st_tags.get(sc) or {}
            out.append({"code": sc, "name": names.get(sc) or sc,
                        "delta_pp": round(dv / T1 * 1000.0, 4),
                        "px_pct": px_pct, "amv_pct": amv_pct,
                        "driver": st.get("tag"), "dir": st.get("dir")})
        return out
    except Exception:  # noqa: BLE001
        return []


def build_contrib(ctx: Any, code: str, date: Optional[str], period: int) -> Dict[str, Any]:
    """GET /contrib ← 旧 8002 `api_contrib`：AMV 变化五档分解（涨幅驱动 vs 换手驱动）。

    口径唯一源 = `夏天-activity/工具/amv_contrib.py`（经 `ctx.lib("amv_contrib")` 加载，
    CONTRACT §8.1 口径库白名单）。本函数只负责取数与拼装，**不重写任何公式**。

    parts 单位 ‰（相对全市场期末总盘 T1）；逐股五项加总恒等于 ΔAMV，
    再加 `den`（大盘基准）后 `cover == total`（旧端同式，已逐值比对）。
    """
    lib = ctx.lib(CONTRIB_LIB)
    with _db_conn(ctx, AMV_DB) as conn:
        lv, code = _resolve_level(conn, code, None)
        ds = _resolve_date(conn, date)
        if not ds:
            return {"ok": False, "reason": "no data"}
        dates = _window_dates(conn, ds, period + 1)
        if len(dates) < 2:
            return {"ok": False, "reason": "交易日不足"}
        d1, d0 = dates[0], dates[-1]
        meta = _fetch(conn, "SELECT name FROM sector_meta WHERE level=? AND code=?", (lv, code))
        name = meta[0]["name"] if meta else None
    codes = _member_codes(ctx, lv, code)
    if not codes:
        return {"ok": False, "reason": "无成分股"}
    with _db_conn(ctx, AMV_DB) as conn:
        try:
            r = lib.board_parts(conn, codes, d0, d1)
        except Exception as exc:  # noqa: BLE001 - 不拖垮卡片
            return {"ok": False, "reason": "%s: %s" % (type(exc).__name__, exc)}
    if not r:
        return {"ok": False, "reason": "缺基准（daily_total 缺日）"}
    # 结构指标 + Top 贡献股（2026-10-27 补全，对齐参考站点「资金结构」）
    with _db_conn(ctx, AMV_DB) as conn:
        struct = _contrib_struct(ctx, conn, codes, d0, d1)
        top = _contrib_top(ctx, conn, code, d1, d0, d1)
        # 主人口径（2026-10-27）：只比两个百分比，不做分项拆解。
        #   amv_pct = 板块 AMV 涨幅；px_pct = 板块指数涨幅（与表内「真实涨跌」同源）
        #   gap     = amv_pct − px_pct，即换手贡献（pp）
        # tags = 四类徽标（齐升/虚涨/独涨/齐跌）
        bf = _fetch(conn, "SELECT close FROM board_flow WHERE board_code=? AND date=?", (code, d0))
        bf1 = _fetch(conn, "SELECT close FROM board_flow WHERE board_code=? AND date=?", (code, d1))
        amv_pct = ((r["amv1"] / r["amv0"] - 1) * 100) if (r.get("amv0") and r["amv1"]) else None
        px_pct = None
        if bf and bf1 and bf[0]["close"] and bf1[0]["close"]:
            px_pct = (bf1[0]["close"] / bf[0]["close"] - 1) * 100
        try:
            cmp_tag = ctx.lib(STRUCT_LIB).compute_row(amv_pct, px_pct)
        except Exception:  # noqa: BLE001
            cmp_tag = None
    return {"ok": True, "code": code, "level": lv, "d0": d0, "d1": d1, "period": period,
            "name": name, "n": r["n"], "n_bj": r["n_bj"],
            "amv0": r["amv0"], "amv1": r["amv1"], "all_pct": r["all_pct"],
            "amv_pct": round(amv_pct, 2) if amv_pct is not None else None,
            "px_pct": round(px_pct, 2) if px_pct is not None else None,
            "cmp": cmp_tag,
            "struct": struct, "top": top,
            "parts": {k: round(r[k], 4) for k in
                      ("px", "sh", "act", "new", "susp", "den", "total", "cover")}}


def build_rank_recent(ctx: Any, level: str, days: int) -> Dict[str, Any]:
    """GET /rank_recent：最近 N 日该层 share 矩阵（旧 8002 api_rank_recent）。

    带进程内缓存（见 `_RECENT_CACHE` 注释）：键 = (最新交易日, 层, 天数)。
    """
    with _db_conn(ctx, AMV_DB) as conn:
        ds = _resolve_date(conn, None)
        if not ds:
            return {"dates": [], "total": [], "level": level, "codes": [], "weight": []}
        ck = ("rr", ds, level, days, _db_stamp(conn))
        hit = _RECENT_CACHE.get(ck)
        if hit is not None:
            return hit
        dates = list(reversed(_window_dates(conn, ds, days)))
        meta = _fetch(conn, "SELECT code, name FROM sector_meta WHERE level=? ORDER BY ord", (level,))
        by_day = {d: {} for d in dates}
        for r in _fetch(conn, "SELECT date, code, share FROM sector_daily WHERE level=? AND date>=? AND date<=?",
                        (level, dates[0], dates[-1])):
            by_day.setdefault(r["date"], {})[r["code"]] = r["share"]
        weight = [[_weight_int(by_day[d].get(m["code"])) for m in meta] for d in dates]
        total = [_amv_int(r["total"]) for r in _fetch(
            conn, "SELECT date, total FROM daily_total WHERE date>=? AND date<=? ORDER BY date", (dates[0], dates[-1]))]
    out = {"dates": dates, "total": total, "level": level,
           "codes": [m["code"] for m in meta], "names": [m["name"] for m in meta],
           "weight": weight}
    if len(_RECENT_CACHE) >= _CACHE_MAX:
        _RECENT_CACHE.clear()
    _RECENT_CACHE[ck] = out
    return out


def build_leaders(ctx: Any, code: str, days: int, top_n: int) -> Dict[str, Any]:
    """GET /leaders/{code}：stock_top 逐日 TOP N（旧 8002 api_leaders）。"""
    with _db_conn(ctx, AMV_DB) as conn:
        ds = _resolve_date(conn, None)
        if not ds:
            return {"code": code, "dates": [], "rows": []}
        dates = list(reversed(_window_dates(conn, ds, days)))
        rows_by_date = {d: [] for d in dates}
        for r in _fetch(conn, "SELECT date, stock_code, stock_name, amt, contrib, rank FROM stock_top "
                             "WHERE sector_code=? AND date>=? AND date<=? ORDER BY date, rank",
                        (code, dates[0], dates[-1])):
            if r["date"] in rows_by_date and len(rows_by_date[r["date"]]) < top_n:
                rows_by_date[r["date"]].append(r)
    return {"code": code, "dates": dates, "top_n": top_n,
            "rows": [{"date": d, "rows": rows_by_date[d]} for d in dates]}


def build_manifest(ctx: Any) -> Dict[str, Any]:
    """GET /manifest：可用日期与分类计数（旧 8002 api_manifest 的只读子集）。"""
    with _db_conn(ctx, AMV_DB) as conn:
        ds = _resolve_date(conn, None)
        dates = list(reversed(_window_dates(conn, ds, 250))) if ds else []
        counts = {}
        for lv in LEVELS:
            r = _fetch(conn, "SELECT COUNT(*) AS n FROM sector_meta WHERE level=?", (lv,))
            counts[lv] = r[0]["n"] if r else 0
        total = _amv_int(_fetch(conn, "SELECT total FROM daily_total WHERE date=?", (ds,))[0]["total"]) if ds else None
    return {"dates": dates, "n_days": len(dates), "latest": ds, "total": total,
            "counts": counts, "coverage": {"days": len(dates), "levels": list(LEVELS)}}


def build_series(ctx: Any, code: str, level: Optional[str], days: int, period: int) -> Dict[str, Any]:
    """GET /series ← 旧 8002 GET /api/series（api_series2）。"""
    with _db_conn(ctx, AMV_DB) as conn:
        lv, code = _resolve_level(conn, code, level)
        rows = _fetch(conn,
                      "SELECT date, name, amv, share, rank FROM sector_daily "
                      "WHERE level=? AND code=? ORDER BY date DESC LIMIT ?", (lv, code, days))
        rows.reverse()
        if not rows:
            return {"error": "not found", "code": code, "level": lv}
        amv = [r["amv"] / 1e8 for r in rows]
        ma = [sum(amv[max(0, i - MA_N + 1):i + 1]) / len(amv[max(0, i - MA_N + 1):i + 1])
              for i in range(len(amv))]
        meta = _meta_map(conn, lv).get(code, {})
        ds = rows[-1]["date"]
        fl = _board_flow(conn, ds, period, lv, {code})
        if code == "ALL":
            _apply_all_real_pct(ctx, conn, fl, ds, period)   # 全A 换 880008 等权（详见函数注释）
    members = _pool_members(ctx)
    return {"code": code, "level": lv, "name": rows[-1]["name"] or meta.get("name"),
            "l1": meta.get("l1"), "ma_n": MA_N, "n_members": members.get(code),
            "dates": [r["date"] for r in rows],
            "amv": [_amv_int(v * 1e8) for v in amv],
            "ma": [_amv_int(v * 1e8) for v in ma],
            "weight": [_weight_int(r["share"]) for r in rows],
            "rank": [r["rank"] for r in rows],
            "real_pct": fl["real_pct"].get(code),
            "amv_pct": fl["amv_pct"].get(code),
            "diverge": fl["diverge"].get(code),
            "flow_calc": fl["flow_calc"].get(code),
            "flow_main": fl["flow_main"].get(code),
            "period": period,
            "unit": "flow_calc=亿元（涨跌成交额差·自算）｜flow_main=万元（主力动向·eltdx）"}


# ---------------------------------------------------------------- 分钟级 AMV（amv_minute.db）

def _minute_db(ctx: Any) -> Any:
    getter = getattr(ctx, "db_read", None)
    if not callable(getter):
        raise HTTPException(status_code=500, detail="ctx.db_read 不可用")
    try:
        return getter(MINUTE_DB)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="%s 不可读: %s" % (MINUTE_DB, exc))


def _minute_dates(conn: Any) -> List[str]:
    return [r["date"] for r in _fetch(
        conn, "SELECT DISTINCT date FROM sector_minute ORDER BY date DESC")]


def _minute_base_share(conn_m: Any, conn_d: Any, date8: str, code: str, level: str,
                       period: int) -> Optional[float]:
    """轮动基准：N 日前收盘的日线 share（旧 8002 `_minute_base_share` 同式）。

    取 date 当日及之前最近 period+1 条日线，基准 = 第 period+1 条（N 日前收盘）；
    历史不足用最早可得日；无日线返回 None（前端显示 0 基准）。
    """
    d8 = date8 if len(date8) == 8 else date8.replace("-", "")
    dash = "%s-%s-%s" % (d8[:4], d8[4:6], d8[6:])
    rows = _fetch(conn_d, "SELECT date, share FROM sector_daily WHERE level=? AND code=? AND date<=? "
                          "ORDER BY date DESC", (level, code, dash))
    if len(rows) > period:
        return float(rows[period]["share"])
    if rows:
        return float(rows[-1]["share"])
    return None


def build_minute_rank(ctx: Any, date: Optional[str], level: str, n: int) -> Dict[str, Any]:
    """GET /minute_rank ← 旧 8002 `api_minute_rank`（最近一个时点的板块 AMV 排名）。"""
    with _db_conn(ctx, MINUTE_DB) as conn:
        if not date:
            date = _fetch(conn, "SELECT MAX(date) AS d FROM sector_minute")[0]["d"]
            if not date:
                return {"error": "无分钟数据"}
        d8 = date.replace("-", "")
        latest = _fetch(conn, "SELECT MAX(hhmm) AS h FROM sector_minute WHERE date=? AND level=?",
                        (d8, level))
        latest = latest[0]["h"] if latest else None
        if not latest:
            return {"error": "无 %s %s 分钟数据" % (d8, level)}
        rows = _fetch(conn, "SELECT code, name, amv, share FROM sector_minute "
                            "WHERE date=? AND hhmm=? AND level=? ORDER BY amv DESC LIMIT ?",
                      (d8, latest, level, n))
    return {"date": d8, "hhmm": latest, "level": level,
            "rows": [{"code": r["code"], "name": r["name"],
                      "amv": round(r["amv"], 0) if r["amv"] is not None else None,
                      "share": round(r["share"], 6) if r["share"] is not None else None} for r in rows]}


def build_minute_series(ctx: Any, code: str, level: str, date: Optional[str],
                        period: int) -> Dict[str, Any]:
    """GET /minute_series ← 旧 8002 `api_minute_series`（单板块分钟 AMV 分时序列）。

    `d_share` = share(t) − 基准 share（N 日前收盘），单位百分点（×100 后）。
    """
    if not code:
        raise HTTPException(status_code=400, detail="缺 code 参数")
    with _db_conn(ctx, MINUTE_DB) as conn:
        if not date:
            date = _fetch(conn, "SELECT MAX(date) AS d FROM sector_minute")[0]["d"]
        d8 = str(date).replace("-", "")
        rows = _fetch(conn, "SELECT hhmm, amv, share, name FROM sector_minute "
                            "WHERE date=? AND code=? AND level=? ORDER BY hhmm",
                      (d8, code, level))
    if not rows:
        return {"error": "无 %s %s 分钟数据" % (code, d8)}
    name = rows[0].get("name") or code
    with _db_conn(ctx, AMV_DB) as conn_d:
        base = _minute_base_share(None, conn_d, d8, code, level, period)
    series = [{
        "hhmm": r["hhmm"],
        "amv": round(r["amv"], 0) if r["amv"] is not None else None,
        "share": round(r["share"], 6) if r["share"] is not None else None,
        "d_share": round((r["share"] - base) * 100, 4)
                   if (base is not None and r["share"] is not None) else None,
    } for r in rows]
    return {"code": code, "name": name, "date": d8, "level": level,
            "period": period, "base_share": base, "series": series}


def build_minute_multi(ctx: Any, codes: List[str], level: str, date: Optional[str],
                       period: int) -> Dict[str, Any]:
    """GET /minute_multi ← 旧 8002 `api_minute_multi`（多板块叠加）。"""
    if not codes:
        raise HTTPException(status_code=400, detail="缺 codes 参数")
    with _db_conn(ctx, MINUTE_DB) as conn:
        if not date:
            date = _fetch(conn, "SELECT MAX(date) AS d FROM sector_minute")[0]["d"]
        d8 = str(date).replace("-", "")
        out: Dict[str, Any] = {}
        with _db_conn(ctx, AMV_DB) as conn_d:
            for code in codes:
                rows = _fetch(conn, "SELECT hhmm, amv, share, name FROM sector_minute "
                                    "WHERE date=? AND code=? AND level=? ORDER BY hhmm",
                              (d8, code, level))
                if not rows:
                    continue
                base = _minute_base_share(None, conn_d, d8, code, level, period)
                out[code] = {
                    "name": rows[0].get("name") or code,
                    "base_share": base,
                    "data": [{
                        "hhmm": r["hhmm"],
                        "amv": round(r["amv"], 0) if r["amv"] is not None else None,
                        "share": round(r["share"], 6) if r["share"] is not None else None,
                        "d_share": round((r["share"] - base) * 100, 4)
                                   if (base is not None and r["share"] is not None) else None,
                    } for r in rows],
                }
    return {"date": d8, "level": level, "period": period, "series": out}


def build_minute_dates(ctx: Any) -> Dict[str, Any]:
    """GET /minute_dates ← 旧 8002 `api_minute_dates`（有分钟数据的日期列表）。"""
    with _db_conn(ctx, MINUTE_DB) as conn:
        return {"dates": _minute_dates(conn)}


# ---------------------------------------------------------------- 注册

def register(ctx: Any) -> APIRouter:
    """插件入口：返回挂到 /api/p/sector 的 APIRouter（核心负责前缀）。"""
    router = APIRouter()

    @router.get("/tree")
    def tree() -> Dict[str, Any]:
        return build_tree(ctx)

    @router.get("/catalog")
    def catalog() -> Dict[str, Any]:
        return build_tree(ctx)

    @router.get("/search")
    def search(q: str = Query(default=""), limit: int = Query(default=20)) -> Dict[str, Any]:
        return build_search(ctx, q, max(1, min(100, int(limit or 20))))

    @router.get("/members/{code}")
    def members(code: str,
                date: Optional[str] = Query(default=None),
                limit: int = Query(default=0),
                full: bool = Query(default=False),
                period: int = Query(default=1)) -> Dict[str, Any]:
        # full=1：limit=0 → 不裁剪（INDEX 层同旧端默认 60）；full=0：limit=0 → 200
        lim = max(0, min(1000, int(limit or 0)))
        return build_members(ctx, code, date, lim, full=bool(full), period=_norm_period(period))

    @router.get("/rank_recent")
    def rank_recent(level: str = Query(default="L2"), days: int = Query(default=80)) -> Dict[str, Any]:
        return build_rank_recent(ctx, _norm_level(level), max(1, min(500, int(days or 80))))

    @router.get("/manifest")
    def manifest() -> Dict[str, Any]:
        return build_manifest(ctx)

    @router.get("/leaders/{code}")
    def leaders(code: str, days: int = Query(default=20), top_n: int = Query(default=10)) -> Dict[str, Any]:
        return build_leaders(ctx, code, max(1, min(250, int(days or 20))), max(1, min(50, int(top_n or 10))))

    @router.get("/rank")
    def rank(date: Optional[str] = Query(default=None),
             level: str = Query(default="L2"),
             period: int = Query(default=1)) -> Dict[str, Any]:
        return build_rank(ctx, date, _norm_level(level), _norm_period(period))

    @router.get("/series")
    def series(code: str = Query(...),
               level: Optional[str] = Query(default=None),
               days: int = Query(default=DEFAULT_DAYS),
               period: int = Query(default=1)) -> Dict[str, Any]:
        d = max(1, min(MAX_DAYS, int(days or DEFAULT_DAYS)))
        return build_series(ctx, code, level, d, _norm_period(period))

    @router.get("/minute_rank")
    def minute_rank(date: Optional[str] = Query(default=None),
                    level: str = Query(default="L2"),
                    n: int = Query(default=20)) -> Dict[str, Any]:
        return build_minute_rank(ctx, date, _norm_level(level), max(1, min(500, int(n or 20))))

    @router.get("/minute_series")
    def minute_series(code: str = Query(...),
                      level: str = Query(default="L2"),
                      date: Optional[str] = Query(default=None),
                      period: int = Query(default=1)) -> Dict[str, Any]:
        return build_minute_series(ctx, code, _norm_level(level), date, _norm_period(period))

    @router.get("/minute_multi")
    def minute_multi(codes: str = Query(default=""),
                     level: str = Query(default="L2"),
                     date: Optional[str] = Query(default=None),
                     period: int = Query(default=1)) -> Dict[str, Any]:
        want = [c.strip() for c in str(codes or "").split(",") if c.strip()]
        return build_minute_multi(ctx, want[:30], _norm_level(level), date, _norm_period(period))

    @router.get("/minute_dates")
    def minute_dates() -> Dict[str, Any]:
        return build_minute_dates(ctx)

    @router.get("/rank_top")
    def rank_top(date: Optional[str] = Query(default=None),
                 level: str = Query(default="L2"),
                 n: int = Query(default=1)) -> Dict[str, Any]:
        return build_rank_top(ctx, date, _norm_level(level), max(1, min(20, int(n or 1))))

    @router.get("/trend")
    def trend(code: str = Query(...), days: int = Query(default=120)) -> Dict[str, Any]:
        return build_trend(ctx, code, max(1, min(MAX_DAYS, int(days or 120))))

    @router.get("/board_top")
    def board_top(sector: str = Query(...),
                  date: Optional[str] = Query(default=None),
                  top: int = Query(default=8)) -> Dict[str, Any]:
        return build_board_top(ctx, sector, date, max(1, min(50, int(top or 8))))

    @router.get("/board_flow")
    def board_flow(date: Optional[str] = Query(default=None),
                   period: int = Query(default=1),
                   level: Optional[str] = Query(default=None)) -> Dict[str, Any]:
        lv = _norm_level(level) if level else None
        return build_board_flow(ctx, date, _norm_period(period), lv)

    @router.get("/contrib")
    def contrib(code: str = Query(...),
                date: Optional[str] = Query(default=None),
                period: int = Query(default=1)) -> Dict[str, Any]:
        return build_contrib(ctx, code, date, _norm_period(period))

    @router.get("/score_trail")
    def score_trail(code: str = Query(...),
                    period: int = Query(default=1),
                    days: int = Query(default=SCORE_TRAIL_DAYS)) -> Dict[str, Any]:
        d = max(1, min(60, int(days or SCORE_TRAIL_DAYS)))
        return build_score_trail(ctx, code, _norm_period(period), d)

    return router
