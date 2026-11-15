# -*- coding: utf-8 -*-
"""watchlist 插件：只读自选池（data/panel/watchlist.json）。

只通过 ctx.panel_json 读取；不写文件、不连库、不接交易。
"""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Query

PANEL = "watchlist"
PANEL_QUOTES = "watchlist_quotes"
PANEL_LEVELS = "focus_levels"


def _load_named(ctx: Any, name: str) -> Dict[str, Any]:
    """通用 panel JSON 读取（白名单校验在 core 的 ctx.panel_json 里做）。"""
    getter = getattr(ctx, "panel_json", None)
    if not callable(getter):
        raise HTTPException(status_code=500, detail="ctx.panel_json 不可用")
    try:
        return getter(name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail="%s 不存在: %s" % (name, exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="%s 读取失败: %s" % (name, exc))


def _load(ctx: Any) -> Dict[str, Any]:
    return _load_named(ctx, PANEL)


def _items(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [x for x in (data.get("items") or []) if isinstance(x, dict)]


def _sorted(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """排序（与旧 8800 `watchlist.json` 实测一致）：先按 kind（股票在前、板块在后），

    再按 order 升序，最后 code 兵底（同 order 时稳定）。只按 order 会把板块插进股票中间，
    与旧端顺序不同（2026-10-22 实测捕获）。
    """
    kind_rank = {"stock": 0, "sector": 1}
    return sorted(items, key=lambda x: (kind_rank.get(str(x.get("kind")), 9),
                                        x.get("order") or 0, x.get("code") or ""))


def build_summary(ctx: Any) -> Dict[str, Any]:
    data = _load(ctx)
    items = _items(data)
    groups = [g for g in (data.get("groups") or []) if isinstance(g, dict)]
    by_kind: Dict[str, int] = {}
    by_group: Dict[str, int] = {}
    for x in items:
        by_kind[str(x.get("kind") or "unknown")] = by_kind.get(str(x.get("kind") or "unknown"), 0) + 1
        by_group[str(x.get("group") or "ungrouped")] = by_group.get(str(x.get("group") or "ungrouped"), 0) + 1
    return {
        "meta": data.get("meta") or {},
        "counts": {"groups": len(groups), "items": len(items), "by_kind": by_kind, "by_group": by_group},
        "groups": groups,
        "updated_at": (data.get("meta") or {}).get("updated_at"),
        "read_only": True,
    }


def build_items(ctx: Any, kind: str | None, group: str | None, limit: int) -> Dict[str, Any]:
    data = _load(ctx)
    items = _sorted(_items(data))
    if kind:
        items = [x for x in items if str(x.get("kind")) == kind]
    if group:
        items = [x for x in items if str(x.get("group")) == group]
    return {"kind": kind, "group": group, "n": len(items), "items": items[:limit]}


def _load_quotes(ctx: Any) -> Dict[str, Any]:
    getter = getattr(ctx, "panel_json", None)
    if not callable(getter):
        raise HTTPException(status_code=500, detail="ctx.panel_json 不可用")
    try:
        return getter(PANEL_QUOTES)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail="watchlist_quotes 不存在: %s" % exc)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="watchlist_quotes 读取失败: %s" % exc)


def build_quotes(ctx: Any) -> Dict[str, Any]:
    """GET /quotes ← `data/panel/watchlist_quotes.json`（tick_feed 60s 一轮落盘）。

    只做转发：不改字段、不算派生指标。`quote_source` 与 `asof` 是新鲜度依据，
    前端必须先看 `asof` 再读数（旧 8800 同口径）。
    """
    data = _load_quotes(ctx)
    quotes = data.get("quotes") if isinstance(data.get("quotes"), dict) else {}
    return {"asof": data.get("asof"), "meta": data.get("meta") or {}, "n": len(quotes),
            "quotes": quotes}


def build_items_with_quotes(ctx: Any, kind: str | None, group: str | None,
                            limit: int) -> Dict[str, Any]:
    """`/items?quotes=1`：条目 + 行情拼接（**只拼接，不重算**）。

    行情源 = `watchlist_quotes.quotes[code]`（含 price/pre_close/change_pct/quote_time）。
    自选池里可能有板块（kind=sector），板块没有 quote → `quote=None`。
    """
    base = build_items(ctx, kind, group, limit)
    try:
        q = _load_quotes(ctx)
    except HTTPException:
        q = {}
    quotes = q.get("quotes") if isinstance(q.get("quotes"), dict) else {}
    for item in base["items"]:
        item["quote"] = quotes.get(str(item.get("code")))
    base["quote_asof"] = q.get("asof")
    return base


def build_levels(ctx: Any, code: str | None = None, only_effective: bool = True) -> Dict[str, Any]:
    """GET /levels ← 关注池关键位 `data/panel/focus_levels.json`（只读）。

    关注池 = 自选股票 ∪ 实盘持仓（排除板块与非权益抵押位），由 `focus_levels_builder` 产出。
    关键位 = 黄白线 + 动能K；`effective=false` 是已破档（保留供回看），默认只返有效档。
    口径唯一源是 builder，本端点**只过滤与拼装，不重算任何价位**。
    """
    data = _load_named(ctx, PANEL_LEVELS)
    items = [x for x in (data.get("items") or []) if isinstance(x, dict)]
    if code:
        want = str(code)
        items = [x for x in items if str(x.get("code")) == want]
    out = []
    for x in items:
        levels = [lv for lv in (x.get("key_levels") or []) if isinstance(lv, dict)]
        if only_effective:
            # 无 effective 字段的历史档按「有效」处理（与旧 8800 同兼容策略）
            levels = [lv for lv in levels if lv.get("effective", True)]
        out.append({"code": x.get("code"), "name": x.get("name"),
                    "origin": x.get("origin") or [],
                    "price": x.get("price"), "price_source": x.get("price_source"),
                    "bar_asof": x.get("bar_asof"), "key_levels": levels})
    return {"meta": data.get("meta") or {}, "quote_asof": data.get("quote_asof"),
            "n": len(out), "items": out}


def register(ctx: Any) -> APIRouter:
    router = APIRouter()

    @router.get("/summary")
    def summary() -> Dict[str, Any]:
        return build_summary(ctx)

    @router.get("/items")
    def items(kind: str | None = Query(default=None),
              group: str | None = Query(default=None),
              quotes: bool = Query(default=False),
              limit: int = Query(default=200)) -> Dict[str, Any]:
        lim = max(1, min(1000, int(limit or 200)))
        if quotes:
            return build_items_with_quotes(ctx, kind, group, lim)
        return build_items(ctx, kind, group, lim)

    @router.get("/quotes")
    def quotes() -> Dict[str, Any]:
        return build_quotes(ctx)

    @router.get("/levels")
    def levels(code: str | None = Query(default=None),
               effective_only: bool = Query(default=True)) -> Dict[str, Any]:
        return build_levels(ctx, code, bool(effective_only))

    @router.get("/groups")
    def groups() -> Dict[str, Any]:
        data = _load(ctx)
        return {"groups": [g for g in (data.get("groups") or []) if isinstance(g, dict)]}

    return router
