# -*- coding: utf-8 -*-
"""alert 插件后端 · 预警页（2026-11-07 预警页重建任务书 §二）

## 只做三个端点，读写各一条主通道

| 路径 | 方法 | 说明 |
|---|---|---|
| `/alert/list` | GET | **唯一读端点**：条件 + 状态 + 当日流水 + 统计 + 枚举表 + 今日异动（只读） |
| `/alert/write` | POST | **唯一写端点**：action = add / remove / toggle / channels |
| `/alert/health` | GET | 只读诊断（行情覆盖、QQ 通道状态），给页面底部一行小字用 |

挂载：`backend.mount = "plugin"`（即 `/api/p/alert/alert/*`）——与本插件自己的前端同一个前缀，
K 线右键设预警也打这个前缀；不再占根路径 `/api/alert/*`（那是旧版 K 线右键专用口，已废）。

写操作**不自己动 JSON**：一律走 `engine/tools/alert_store.py`（跨进程锁 + 原子写 + 流水），
条目结构一律由 `engine/tools/add_alert.py` 生成与自检。本文件只做 HTTP 参数整形。

## 边界（任务书 §二.2 硬约束 + 测试守门）

1. **只提醒不下单**：本插件不 import `cond_watch` / `app.core.paper` / broker，不写任何委托账本；
   `test_alert_page.py::test_backend_never_imports_order_chain` 守门。
2. **不碰持仓与账户**（§三）：列表里没有仓位/账户字段，也不读 `real_positions`。
3. **出口互斥**：系统级类型（L1_TYPES）只允许 `exit=L1`；个股类型只允许 `exit=L2`。
   入口就挡掉，页面选错也写不进去。

## 行情覆盖提示（不是拒绝）

预警评估的现价来自 `data/panel/watchlist_quotes.json`（tick_feed 每 60s 一轮、覆盖自选池 +
已订阅的预警票）。票不在覆盖面 → 条目照写（主人意图不丢），但返回 `quote_covered=false`
+ `warnings`，页面在该条上挂一个「无行情」角标。
"""
from __future__ import annotations

import argparse as _ap
import json
import os
import sys
import threading
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Query

# 2026-11-15 公开化改造：不再写死本机路径，一律由 core.paths 推导（支持 PANEL_V2_WS 覆盖）。
# 旧引擎目录（engine/）是预警条目的唯一实现源（add_alert / alert_store / common.alert_schema）；
# 它不存在时本插件加载失败——core 侧只 warning 不中断启动，其余插件照常。
from app.core import paths as _paths  # noqa: E402

WS = str(_paths.WS)
ENGINE = str(_paths.WS / "engine")
for _p in (ENGINE, os.path.join(ENGINE, "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import add_alert as AA  # noqa: E402  条目结构与阈值算法唯一实现
import alert_store as STORE  # noqa: E402  落盘唯一写入口
from common import alert_schema as AS  # noqa: E402  三张枚举唯一源

PANEL_DIR = str(_paths.PANEL_DIR)
PERSISTENT = os.path.join(PANEL_DIR, "alert_plan_persistent.json")
QUOTES = os.path.join(PANEL_DIR, "watchlist_quotes.json")
FOCUS = os.path.join(PANEL_DIR, "focus_levels.json")
INTRADAY = os.path.join(PANEL_DIR, "intraday.json")
ALERT_TODO = os.path.join(PANEL_DIR, "alert_todo.json")

_FOCUS_CACHE = {"ts": 0.0, "map": {}}


def _focus_map() -> Dict[Any, Any]:
    """(code, level_type) → 防线价（来自关注池关键位，30s 缓存）。

    为什么要它：白线/黄线/动能K 的价格每天变，预警表里不冻结；页面要算「离防线多远」
    只能拿当日的线值——focus_levels 正是盘后算好的那张表，直接读、不重算。"""
    import time  # noqa: PLC0415
    now = time.time()
    if now - float(_FOCUS_CACHE["ts"]) < 30 and _FOCUS_CACHE["map"]:
        return _FOCUS_CACHE["map"]
    doc = _read_json(FOCUS)
    m: Dict[Any, Any] = {}
    for it in doc.get("items") or []:
        if not isinstance(it, dict):
            continue
        code = str(it.get("code") or "")
        for lv in it.get("key_levels") or []:
            if not isinstance(lv, dict):
                continue
            price = lv.get("price")
            if isinstance(price, (int, float)) and price > 0:
                m[(code, str(lv.get("level_type") or ""))] = float(price)
    _FOCUS_CACHE.update(ts=now, map=m)
    return m


def _index_pct(code: str) -> Any:
    doc = _read_json(INTRADAY)
    for q in doc.get("market_quotes") or []:
        if isinstance(q, dict) and str(q.get("code")) == code:
            return q.get("change_pct")
    return None


def _level_for(p: Dict[str, Any], t0: Dict[str, Any], focus: Dict[Any, Any]) -> Any:
    """这条预警对应的「防线价」：动态线取当日线值，固定价取存下的档位价。

    优先级：关注池当日线值 → 建条时存的档位价 → 条件阈值 → 内核上一轮算出的线值。
    最后一级很关键：白线/黄线在关注池里未必有档（只有 11 条 WHITE），但内核每轮都会算，
    不拿它兜底就会出现「白线」后面光秃秃没有距离。
    """
    code = str(t0.get("code") or "")
    kind = AS.kind_of(p)
    spec = p.get("spec") or {}
    cand = None
    if kind == "LINE_BELOW":
        lt = "WHITE" if str(t0.get("line")) == "white" else ("YELLOW" if str(t0.get("line")) == "yellow" else "")
        cand = focus.get((code, lt)) or p.get("level_price")
    elif kind == "PULLBACK_MA":
        rk = str(spec.get("resolved_kind") or "")
        lt = "WHITE" if rk == "white" else ("YELLOW" if rk == "yellow" else "")
        cand = focus.get((code, lt)) or p.get("level_price")
    elif kind == "PULLBACK_KEYK":
        rk = str(spec.get("resolved_kind") or "")
        lt = "EK_CLOSE" if rk == "ek_close" else ("EK_OPEN" if rk == "ek_open" else "")
        cand = focus.get((code, lt)) or p.get("level_price")
    else:
        lp = p.get("level_price")
        if isinstance(lp, (int, float)) and lp > 0:
            cand = lp
        else:
            v = t0.get("value")
            if isinstance(v, (int, float)) and v > 0:
                cand = v
    if isinstance(cand, (int, float)) and cand > 0:
        return float(cand)
    le = (p.get("last_eval") or {}).get("level")
    if isinstance(le, (int, float)) and le > 0:
        return float(le)
    return None


def _badge(p: Dict[str, Any], t0: Dict[str, Any], quotes: Dict[str, Any],
           focus: Dict[Any, Any]) -> Dict[str, Any]:
    """徽标文案 = 防线名 + 当前距离（不外露阀值数字）。

    - 价格/线类：(现价 - 防线价) / 防线价 × 100，正数=在防线上方；
    - 涨跌幅类：直接给当前涨跌幅（它本身就是百分比），如「跌幅 -3.0%」；
    - 取不到行情/防线价：只给防线名，不编数字。
    """
    kind = AS.kind_of(p)
    code = str(t0.get("code") or "")
    tag = str(p.get("level_tag") or "")
    if not tag:
        tag = AA.TYPE_CN.get(kind, kind)
    q = quotes.get(code) or {}

    if kind == "CHANGE_PCT":
        v = q.get("change_pct")
        return {"text": f"{tag} {float(v):+.1f}%" if isinstance(v, (int, float)) else tag,
                "dist_pct": v if isinstance(v, (int, float)) else None}
    if kind == "INDEX_PCT_BELOW":
        v = _index_pct(code)
        return {"text": f"{tag} {float(v):+.1f}%" if isinstance(v, (int, float)) else tag,
                "dist_pct": v if isinstance(v, (int, float)) else None}

    price = q.get("price")
    if not isinstance(price, (int, float)):
        price = (p.get("last_eval") or {}).get("price")   # 行情文件缺该票 → 用内核上一轮的现价
    level = _level_for(p, t0, focus)
    if isinstance(price, (int, float)) and isinstance(level, (int, float)) and level > 0:
        dist = (float(price) - float(level)) / float(level) * 100.0
        if abs(dist) < 100:
            return {"text": f"{tag} {dist:+.1f}%", "dist_pct": round(dist, 2)}
    return {"text": tag, "dist_pct": None}

_LOCK = threading.Lock()          # 同进程内串行（跨进程锁在 alert_store 里）
#: 均线类必须走动态线（阈值随交易日漂移的老坑，2026-11-04 已裁定）
DYNAMIC_REQUIRED = ("LINE_BELOW", "MA_BELOW", "MA_ABOVE")
#: 页面「新增」表单里给的类型顺序（按使用频率，不是枚举顺序）
FORM_TYPE_ORDER = ("PRICE_BELOW", "PRICE_ABOVE", "CHANGE_PCT", "LINE_BELOW", "MA_BELOW",
                   "MA_ABOVE", "PULLBACK_MA", "PULLBACK_KEYK", "PRICE_TOUCH_HOLD",
                   "LINE_TOUCH_HOLD", "INDEX_PCT_BELOW", "INDEX_PRICE_BELOW",
                   "AMV_LEVEL_BELOW", "AMV_MA_GAP_BELOW", "BREADTH_UP_BELOW")


def _read_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _quotes() -> Dict[str, Any]:
    return (_read_json(QUOTES).get("quotes") or {})


def _plan_name(theme: str, code: str) -> str:
    """从 theme 取股票名（add_alert 生成格式固定为 `{name} …`），取不到回代码。"""
    t = str(theme or "").strip()
    if t:
        head = t.split(" ")[0].strip()
        if head and head != code and len(head) <= 12:
            return head
    return code or "—"


def _delivery_view(p: Dict[str, Any]) -> Dict[str, Any]:
    """命中回执的送达程度（2026-11-07 夏天复核：入队 ≠ 送达）。

    旧记录没有 `status` 时按 ok/msg_id 推断：有 msg_id → 已确认；只 ok → 已入队（不谎报）。
    """
    lh = p.get("last_hit") or {}
    dv = lh.get("delivery") or {}
    exit_ = str(p.get("exit") or AS.EXIT_L2)
    st = AS.delivery_status(dv, exit_)
    ch = dv.get("wake" if exit_ == AS.EXIT_L1 else "qq") or {}
    if not isinstance(ch, dict):
        ch = {}
    return {
        "status": st,
        "status_cn": AS.DELIVERY_CN.get(st, st),
        "msg_id": ch.get("msg_id"),
        "msg_ts": ch.get("msg_ts"),
        "sent_at": ch.get("sent_at"),
        "via": ch.get("via"),
        "ok": ch.get("ok") if ch else None,
        "at": lh.get("ts"),
    }


def _describe(p: Dict[str, Any], quotes: Dict[str, Any],
              focus: Dict[Any, Any] = None) -> Dict[str, Any]:
    """条目 → 页面友好形态（**只读整形**，不改存储结构）。"""
    terms = (p.get("condition") or {}).get("terms") or []
    t0 = terms[0] if terms else {}
    code = str(t0.get("code") or "")
    kind = AS.kind_of(p)
    state = str(p.get("state") or "")
    enabled = p.get("enabled") is not False
    # 防线亮灭：还在待命（未触发）才亮；已命中/已过期/已关闭、或关注池已判「跌破」都是不亮
    broken = bool(p.get("broken_since"))
    lit = bool(enabled and state in (AS.PENDING, AS.MONITORING) and not broken)
    bg = _badge(p, t0, quotes, focus if focus is not None else _focus_map())
    out = {
        "dp_id": p.get("dp_id"),
        "theme": p.get("theme"),
        "name": _plan_name(p.get("theme") or "", code),
        "kind": kind,
        "kind_cn": AA.TYPE_CN.get(kind, kind),
        "code": code,
        "metric": t0.get("metric"),
        "op": t0.get("op"),
        "value": t0.get("value"),
        "line": t0.get("line"),
        "dynamic": bool(t0.get("dynamic")),
        "window": p.get("window"),
        "confirm_points": p.get("confirm_points"),
        "exit": p.get("exit"),
        "exit_cn": AS.EXIT_CN.get(str(p.get("exit")), p.get("exit")),
        "source": p.get("source"),
        "source_cn": AS.SOURCE_CN.get(str(p.get("source")), p.get("source")),
        "origin": AS.origin_of(p),
        "origin_cn": AS.ORIGIN_CN.get(AS.origin_of(p), AS.origin_of(p)),
        "notify": AS.notify_allowed(p),
        "enabled": enabled,
        "state": state,
        "state_cn": AS.STATE_CN.get(state, state),
        "state_since": p.get("state_since"),
        "last_hit": p.get("last_hit"),
        "delivery": _delivery_view(p),
        "spec": p.get("spec"),
        "note": p.get("note") or "",
        "quote_covered": bool(code and code in quotes),
        "badge": bg["text"],
        "dist_pct": bg["dist_pct"],
        "prox": None,
        "hot": False,
        "level_tag": p.get("level_tag") or "",
        "close_review": p.get("close_review"),
        "broken": broken,
        "broken_since": p.get("broken_since"),
        "lit": lit,
    }
    # 靠近防线：5% 内越近越深，1% 内转红（主人 2026-11-07）
    # 只对「还亮着的价格/线类」算；涨跌幅类的数字本身是涨跌幅，不是离防线的距离。
    if lit and kind not in ("CHANGE_PCT", "INDEX_PCT_BELOW"):
        dpv = out.get("dist_pct")
        if isinstance(dpv, (int, float)):
            ad = abs(float(dpv))
            if ad <= 5.0:
                out["prox"] = round(max(0.0, min(1.0, (5.0 - ad) / 4.0)), 2)
                out["hot"] = bool(ad <= 1.0)
    # 收盘确认「回踩不破」→ 徽标尾巴加一个对钩（主人 2026-11-07：收盘后再确认一次）
    cr = p.get("close_review") or {}
    if cr.get("result") == "回踩不破":
        out["badge"] = out["badge"] + " ✓"
        out["held"] = True
    terms_txt = " ".join(
        f"{t.get('metric')}{t.get('op')}{t.get('value')}"
        + (f"(line={t.get('line')})" if t.get("line") else "")
        for t in terms if isinstance(t, dict))
    out["condition_text"] = terms_txt
    return out


def _todo_block() -> Dict[str, Any]:
    """今日异动（只读，来自 alert_todo.json）。

    为什么**不并入**条件表：alert_todo 是 alert_todo_builder 每 5 分钟整表重写的**事件快照**
    （关注池异动 + 待办），不是主人设的条件，没有生命周期。并进去会被整表重写冲掉。
    """
    doc = _read_json(ALERT_TODO)
    alerts = [a for a in (doc.get("alerts") or []) if isinstance(a, dict)]
    todos = [t for t in (doc.get("todos") or []) if isinstance(t, dict)]
    return {
        "updated_at": doc.get("updated_at"),
        "phase": doc.get("phase"),
        "mode": doc.get("mode"),
        "alerts": alerts[:20],
        "todos": [t for t in todos if str(t.get("status") or "OPEN") == "OPEN"][:20],
        "source": "alert_todo.json（只读展示，不并入条件表）",
    }


def _notify_health() -> Dict[str, Any]:
    """QQ 通道状态（只读）：从 core/notify 取，失败不影响页面。"""
    try:
        from app.core import notify  # noqa: PLC0415 - 懒引入，插件按文件加载时也能用
        st = notify.status()
        return {"ok": True, "configured": st.get("configured"),
                "last_ok_ts": st.get("last_ok_ts"), "consecutive_fails": st.get("consecutive_fails", 0),
                "last_error": st.get("last_error")}
    except Exception as e:  # noqa: BLE001 - 诊断失败不阻断页面
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"[:160]}


def _build_args(payload: Dict[str, Any]) -> _ap.Namespace:
    """HTTP payload → add_alert.build_entry 的入参（缺省与 CLI 对齐）。"""
    return _ap.Namespace(
        confirm=max(1, int(payload.get("confirm") or 1)),
        window=str(payload.get("window") or AA.DEFAULT_WINDOW),
        line=str(payload.get("line") or "white"),
        ma=str(payload.get("ma") or "ma5"),
        dir=str(payload.get("dir") or "up"),
        cross_down=bool(payload.get("cross_down")),
        dynamic=bool(payload.get("dynamic")),
        value=payload.get("value"),
        target=str(payload.get("target") or "auto"),
    )


def _add(payload: Dict[str, Any]) -> Dict[str, Any]:
    code = str(payload.get("code") or "").strip()
    atype = str(payload.get("type") or "").strip().upper()
    name = str(payload.get("name") or code).strip()
    source = str(payload.get("source") or AS.SOURCE_SESSION)
    # 触达来源（2026-11-11）：面板表单只能建 manual（主人手加）。review 由复盘链在写接口
    # 显式传 origin=review；页面不提供这个选项，也不允许手动填 auto（auto 是机器派生的）。
    origin = str(payload.get("origin") or AS.ORIGIN_MANUAL).strip().lower()
    if origin not in AS.ALERT_ORIGINS:
        return {"ok": False, "error": "origin 须为 %s" % "/".join(AS.ALERT_ORIGINS)}
    warnings: List[str] = []

    if atype not in AA.ALERT_TYPES:
        return {"ok": False, "error": "type 须为 %s" % "/".join(AA.ALERT_TYPES)}
    if not code and atype not in AA.L1_TYPES:
        return {"ok": False, "error": "缺 code"}
    # 出口互斥（§2.3）：系统级只能唤醒夏天，个股级只能直推主人
    if atype in AA.L1_TYPES:
        ex = AS.EXIT_L1
        if payload.get("exit") and str(payload.get("exit")).upper() != AS.EXIT_L1:
            return {"ok": False, "error": "系统级类型只能挂 L1（唤醒夏天），不能直推主人"}
    else:
        ex = AS.EXIT_L2
        if payload.get("exit") and str(payload.get("exit")).upper() != AS.EXIT_L2:
            return {"ok": False, "error": "个股级类型只能挂 L2（直推主人），不能唤醒夏天"}
    if atype in DYNAMIC_REQUIRED and not payload.get("dynamic"):
        return {"ok": False, "error": "%s 必须 dynamic=true（写 line 不写死 value）" % atype}
    if atype in DYNAMIC_REQUIRED and payload.get("dynamic"):
        payload = {**payload, "dynamic": True}

    value = payload.get("value")
    if atype in ("PRICE_BELOW", "PRICE_ABOVE") or atype in AA.L1_TYPES:
        try:
            value = float(value)
        except (TypeError, ValueError):
            return {"ok": False, "error": "该类型必须给数值（右键价 / 阈值）"}
    if atype in ("PRICE_BELOW", "PRICE_ABOVE") and value <= 0:
        return {"ok": False, "error": "value 必须 > 0"}

    try:
        entry = AA.build_entry(atype, code, name, value, _build_args(payload))
    except Exception as e:  # noqa: BLE001 - 参数不合法按 4xx 返回，不抛 500
        return {"ok": False, "error": "构建条目失败：%r" % e}
    errs = AA.structural_check(entry)
    if errs:
        return {"ok": False, "error": "结构自检不过：" + "；".join(errs)}

    first = (entry.get("condition") or {}).get("terms") or [{}]
    term0 = first[0] if first else {}
    if isinstance(term0.get("value"), (int, float)) and not term0.get("dynamic"):
        conflicts = AA.protection_conflicts(code, term0.get("value"))
        if conflicts:
            return {"ok": False, "error": "与持仓保护价冲突：" + "；".join(conflicts)}

    try:
        code_, plan = STORE.add_plan(entry, source=source, exit_=ex, origin=origin,
                                     note=str(payload.get("note") or ""))
    except STORE.AlertStoreLocked as e:
        return {"ok": False, "error": str(e)}
    if code_ == "duplicate":
        return {"ok": True, "added": False, "duplicate": True, "dp_id": entry["dp_id"],
                "entry": _describe(plan, _quotes()),
                "warnings": ["同 dp_id 已存在，未覆盖（改阈值请先删除）"]}
    if code_ == "error":
        return {"ok": False, "error": plan.get("error")}

    if code and code not in _quotes() and atype not in AA.L1_TYPES:
        warnings.append("该票当前不在行情覆盖面（watchlist_quotes.json），加自选后才会触发")
    return {"ok": True, "added": True, "dp_id": entry["dp_id"], "origin": origin,
            "entry": _describe(plan, _quotes()), "warnings": warnings}


def _review_one(p: Dict[str, Any], quotes: Dict[str, Any], focus: Dict[Any, Any]) -> Any:
    """收盘确认：当天最低碰过防线、收盘又没丢 → 「回踩不破」。

    口径（主人 2026-11-07 定）：
    - 触及：当日最低 ≤ 防线价 + 容差（容差 = max(0.01, 0.1%，同 touch_tolerance）；
    - 不破：收盘（用快照里的最新价，收盘后即收盘价）≥ 防线价；
    - 其余两态：碰了且收在下面 = 跌破；没碰过 = 未触及；缺行情/防线价 = 无数据。
    只给个股写；大盘类（无 low/close）不参与。
    """
    terms = (p.get("condition") or {}).get("terms") or []
    t0 = terms[0] if terms else {}
    code = str(t0.get("code") or "")
    if not code:
        return None
    q = quotes.get(code) or {}
    low = q.get("low")
    close = q.get("price")
    level = _level_for(p, t0, focus)
    base = {"code": code, "low": low, "close": close, "level": level}
    if not isinstance(low, (int, float)) or not isinstance(close, (int, float)) \
            or not isinstance(level, (int, float)) or level <= 0:
        return {**base, "result": "无数据"}
    tol = max(0.01, float(level) * 0.001)
    touched = float(low) <= float(level) + tol
    held = float(close) >= float(level)
    if touched and held:
        res = "回踩不破"
    elif touched and not held:
        res = "跌破"
    else:
        res = "未触及"
    return {**base, "result": res, "touched": touched, "held": held,
            "dist_pct": round((float(close) - float(level)) / float(level) * 100.0, 2)}


def _group_plans(plans: List[Dict[str, Any]], order: Any = None) -> List[Dict[str, Any]]:
    """按标的聚合卡片（2026-11-07 主人要卡片式）。

    排序规则（主人 2026-11-07 04:1x 定）：
    - 手动拖过的顺序（`meta.card_order`）优先，**冻结不改变位置**；
    - 默认：**离防线越近排越前**（取该票所有防线里最近的那道）；算不出距离的（没行情）排最后；
    - 卡片内的徽标**不重排**（按库里条目顺序），否则一冻结就跳位。
    """
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for p in plans:
        key = "__system__" if p.get("exit") == "L1" else (p.get("code") or "__unknown__")
        buckets.setdefault(key, []).append(p)
    groups: List[Dict[str, Any]] = []
    for key, items in buckets.items():
        total = len(items)
        lit = sum(1 for x in items if x["lit"])
        enabled_n = sum(1 for x in items if x["enabled"])
        # 最近一道防线的距离（不管亮着还是已破，都用它；否则一冻结就跳位）
        dists = [abs(float(x["dist_pct"])) for x in items
                 if isinstance(x.get("dist_pct"), (int, float)) and abs(float(x["dist_pct"])) <= 99]
        groups.append({
            "key": key,
            "code": "" if key == "__system__" else key,
            "name": "大盘 / 全A" if key == "__system__" else (items[0].get("name") or key),
            "exit": items[0].get("exit"),
            "plans": items,
            "total": total,
            "lit": lit,
            "push": sum(1 for x in items if x.get("notify")),
            "enabled": enabled_n,
            "frozen": enabled_n == 0,
            "nearest": round(min(dists), 2) if dists else None,
            "last_hit_ts": max([str((x.get("last_hit") or {}).get("ts") or "") for x in items] or [""]),
        })
    idx = {str(k): i for i, k in enumerate(order or []) if isinstance(k, str)}

    def _sk(g: Dict[str, Any]):
        if g["key"] in idx:
            return (0, idx[g["key"]], "", 0.0)
        # 默认：离防线最近的排在前；没行情的（算不出距离）排最后
        if isinstance(g.get("nearest"), (int, float)):
            return (1, 0, "", float(g["nearest"]))
        return (2, 0, g["name"] or "", 0.0)

    groups.sort(key=_sk)
    return groups


def register(ctx: Any) -> APIRouter:
    router = APIRouter()

    @router.get("/alert/list")
    def alert_list() -> Dict[str, Any]:
        quotes = _quotes()
        focus = _focus_map()          # 当日防线价表（白线/黄线/动能K），算「离防线多远」用
        snap = STORE.snapshot()
        plans = [_describe(p, quotes, focus) for p in snap.get("plans") or []]
        plans.sort(key=lambda x: (
            0 if x["state"] in (AS.MONITORING, AS.PENDING) else 1,
            0 if x["exit"] == AS.EXIT_L1 else 1,
            x.get("theme") or ""))
        groups = _group_plans(plans, snap.get("card_order"))
        return {
            "ok": True,
            "trade_date": snap.get("trade_date"),
            "updated_at": snap.get("updated_at"),
            "channels": snap.get("channels"),
            "stats": snap.get("stats"),
            "plans": plans,
            "groups": groups,
            "flow": snap.get("flow") or [],
            "enums": {
                **snap.get("enums", {}),
                "types": [{"code": t, "name": AA.TYPE_CN.get(t, t)} for t in FORM_TYPE_ORDER
                          if t in AA.ALERT_TYPES],
                "type_cn": AA.TYPE_CN,
                "origins": [{"code": o, "name": AS.ORIGIN_CN[o]} for o in AS.ALERT_ORIGINS],
                "notify_origins": sorted(AS.NOTIFY_ORIGINS),
            },
            "todo": _todo_block(),
            "notify": _notify_health(),
            "paths": {"store": PERSISTENT},
        }

    @router.post("/alert/write")
    def alert_write(payload: Optional[dict] = Body(default=None)) -> Dict[str, Any]:
        p = payload or {}
        action = str(p.get("action") or "").strip().lower()
        with _LOCK:
            if action == "add":
                return _add(p)
            if action == "remove":
                dp_id = str(p.get("dp_id") or "").strip()
                if not dp_id:
                    return {"ok": False, "error": "缺 dp_id"}
                try:
                    ok = STORE.remove_plan(dp_id)
                except STORE.AlertStoreLocked as e:
                    return {"ok": False, "error": str(e)}
                return {"ok": bool(ok), "removed": dp_id} if ok else \
                       {"ok": False, "error": "未找到 dp_id：%s" % dp_id}
            if action == "toggle":
                dp_id = str(p.get("dp_id") or "").strip()
                if not dp_id:
                    return {"ok": False, "error": "缺 dp_id"}
                enabled = bool(p.get("enabled"))
                try:
                    plan = STORE.set_enabled(dp_id, enabled)
                except STORE.AlertStoreLocked as e:
                    return {"ok": False, "error": str(e)}
                if plan is None:
                    return {"ok": False, "error": "未找到 dp_id：%s" % dp_id}
                return {"ok": True, "dp_id": dp_id, "enabled": enabled,
                        "state": plan.get("state")}
            if action == "toggle_group":
                # 卡片级「临时冻结 / 恢复」（2026-11-07 主人要的整卡冻结）：一次改一张卡的所有防线
                code = str(p.get("code") or "").strip()
                want_exit = str(p.get("exit") or "").strip().upper()
                enabled = bool(p.get("enabled"))
                if not code and want_exit != AS.EXIT_L1:
                    return {"ok": False, "error": "缺 code（或 exit=L1 操作大盘卡）"}

                def _matches(x: Dict[str, Any]) -> bool:
                    ex = str(x.get("exit") or "").upper()
                    if want_exit == AS.EXIT_L1:
                        return ex == AS.EXIT_L1
                    terms = (x.get("condition") or {}).get("terms") or []
                    return str((terms[0] or {}).get("code") or "") == code

                def _mut(doc: Dict[str, Any], _td: Any) -> List[Dict[str, Any]]:
                    now_s = AS.now_str()
                    for x in doc.get("plans") or []:
                        if not _matches(x):
                            continue
                        target = AS.PENDING if enabled else AS.CLOSED
                        if not AS.transition_allowed(str(x.get("state") or AS.PENDING), target):
                            continue
                        x["enabled"] = enabled
                        if str(x.get("state")) != target:
                            x["state"] = target
                            x["state_since"] = now_s
                        x["updated_at"] = now_s
                    return []

                try:
                    STORE.bulk_update(_mut)
                except STORE.AlertStoreLocked as e:
                    return {"ok": False, "error": str(e)}
                return {"ok": True, "code": code, "exit": want_exit, "enabled": enabled}
            if action == "review_close":
                # 收盘确认（主人 2026-11-07）：当天最低碰过防线、收盘没丢 → 回踩不破
                quotes = _quotes()
                focus = _focus_map()
                qdoc = _read_json(QUOTES)
                today = str(qdoc.get("asof") or (qdoc.get("meta") or {}).get("generated_at") or "")
                today = today[:10].replace("-", "")   # 快照的时间即「哪天的收盘」
                stat = {"全部": 0, "回踩不破": 0, "跌破": 0, "未触及": 0, "无数据": 0}

                def _mut(doc: Dict[str, Any], _td: Any) -> List[Dict[str, Any]]:
                    now_s = AS.now_str()
                    for x in doc.get("plans") or []:
                        if x.get("enabled") is False:
                            continue
                        r = _review_one(x, quotes, focus)
                        if r is None:
                            continue
                        r["date"] = today
                        r["ts"] = now_s
                        x["close_review"] = r
                        stat["全部"] += 1
                        stat[r["result"]] = stat.get(r["result"], 0) + 1
                    return []

                try:
                    STORE.bulk_update(_mut)
                except STORE.AlertStoreLocked as e:
                    return {"ok": False, "error": str(e)}
                return {"ok": True, "trade_date": today, "stats": stat}
            if action == "reorder_cards":
                order = p.get("order")
                if not isinstance(order, list):
                    return {"ok": False, "error": "order 须为数组"}
                try:
                    saved = STORE.set_card_order(order)
                except STORE.AlertStoreLocked as e:
                    return {"ok": False, "error": str(e)}
                return {"ok": True, "count": len(saved)}
            if action == "channels":
                ch = p.get("channels") or {}
                if not isinstance(ch, dict):
                    return {"ok": False, "error": "channels 须为对象"}
                try:
                    cur = STORE.set_channels(ch)
                except STORE.AlertStoreLocked as e:
                    return {"ok": False, "error": str(e)}
                return {"ok": True, "channels": cur}
            return {"ok": False, "error": "action 须为 add/remove/toggle/channels，收到 %r" % action}

    @router.get("/alert/health")
    def alert_health() -> Dict[str, Any]:
        quotes = _quotes()
        plans = STORE.read_doc().get("plans") or []
        uncovered = [str((p.get("condition") or {}).get("terms", [{}])[0].get("code") or "")
                     for p in plans if p.get("enabled") is not False]
        uncovered = [c for c in uncovered if c and c not in quotes and c != "880008.SH"]
        return {"ok": True, "quote_count": len(quotes),
                "plans": len(plans), "uncovered": sorted(set(uncovered)),
                "notify": _notify_health()}

    return router
