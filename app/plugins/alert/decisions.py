# -*- coding: utf-8 -*-
"""decisions.py · 预警卡里的「决策点」分区数据装配（2026-11-16，纯只读）

## 一句话
把决策点链路摊到预警卡上：**今天该守什么**（盘前计划）、**今天真命中了什么**（事件流水）、
**评估器还活着没有**（心跳）、**会不会叫醒夏天**（唤醒总开关）。只展示，不判定。

## 三个只读源（都由生产脚本在写，本文件只读）

| 源 | 文件 | 谁在写 |
|---|---|---|
| 当日计划 | `data/panel/wake_plan_{YYYYMMDD}.json` | `engine/tools/build_wake_plan.py`（盘前） |
| 命中流水 | `engine/state/wake_events.jsonl` | `engine/executor/wake_eval.py`（触发一条追加一行） |
| 评估器心跳 | `engine/state/wake_eval_status.json` | `wake_eval.write_heartbeat`（60s 一跳） |

读法沿用预警卡既有习惯（`backend.py::_read_json` 同款直读，路径由 `app.core.paths` 推导），
不为一张只读分区去改 core 白名单；写**不在这里**——唯一写入口是 `common/wake_switch.py`（引擎侧）。

## 硬口径（主人 2026-11-16 的「口径与边界」，测试守门）

1. **不做二次判定**：命中次数/最后触发时间/触发值一律取流水原值，`trigger_values` 原样透出。
   本文件**不读** intraday.json、**不重算** condition——那会造出第二真相源。
   `condition_text` 只是把 `logic + terms` 字面翻成人话（不含任何现值比较）。
2. **计划条目不标实时状态**：不出现「已满足 / 未满足」，今日是否触发只看流水。
3. **空态必须明确**：计划缺 → `present=false`（reason=not_generated / trade_date_mismatch / unreadable）；
   无事件 → `count=0`（`timeline=[]`，这是正常态）；心跳缺 → `state=UNKNOWN`。
   跨日一律按**今天**对齐（计划看 `meta.trade_date`、事件看 `trade_date`），不拿旧文件充今天。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.core import paths as _paths

PANEL_DIR = str(_paths.PANEL_DIR)
STATE_DIR = str(_paths.WS / "engine" / "state")
PLAN_TPL = "wake_plan_{date8}.json"
EVENTS_FILE = os.path.join(STATE_DIR, "wake_events.jsonl")
STATUS_FILE = os.path.join(STATE_DIR, "wake_eval_status.json")

DAY8_RE = re.compile(r"^\d{8}$")

# 心跳陈旧阈值（秒）：评估器 60s/轮，连续 3 轮没跳就标 stale。
# 这是**心跳新鲜度**，不是条件判定——只用于把「在跑」染色，不改变任何展示值。
HEARTBEAT_STALE_S = 180

# 「最近一份计划」探测回溯天数：只在今天缺计划时探（覆盖国庆/春节 8 连休）。
LATEST_PLAN_PROBE_DAYS = 10

# metric → 中文标签。仅用于把 condition 字面翻成人话；**未登记的原样显示 metric 名**，
# 不猜、不换算（阈值一律按计划里的原值展示：0.4 就是 0.4，不是 40%）。
METRIC_LABELS: Dict[str, str] = {
    "exec_failure": "执行失败数",
    "index_price": "指数价",
    "zt_total": "涨停家数",
    "zhaban_rate": "炸板率",
    "stock_price": "股价",
    "change_pct": "涨跌幅%",
    "line_value": "动态线",
    "price_vs_line": "现价−线值",
    "day_low": "当日最低",
}


# ---------------------------------------------------------------- 基础读写

def _read_text(path: str) -> Optional[str]:
    """整份读文本；不存在/读不了 → None（「今天还没生成」不是异常）。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def _read_json(path: str) -> Optional[dict]:
    text = _read_text(path)
    if text is None:
        return None
    try:
        doc = json.loads(text)
    except (TypeError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _date8(now: datetime) -> str:
    return now.strftime("%Y%m%d")


def _iso(date8: str) -> str:
    return f"{date8[:4]}-{date8[4:6]}-{date8[6:]}"


def _age_s(stamp: Any, now: datetime) -> Optional[int]:
    """'YYYY-MM-DD HH:MM:SS' → 距 now 的秒数；解析失败/空 → None（不猜）。"""
    if not stamp:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return max(0, int((now - datetime.strptime(str(stamp), fmt)).total_seconds()))
        except (TypeError, ValueError):
            continue
    return None


def _fmt_val(value: Any) -> str:
    """阈值/触发值的字面量：数字原样（去尾零），其它原样转字符串。"""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return f"{value:g}"
    if value is None:
        return "—"
    return str(value)


# ---------------------------------------------------------------- 条件/触发值 → 人话（不做判定）

def _term_text(term: Any) -> str:
    """单 term 字面渲染：`标签(代码) 算子 阈值`。不编码任何判定语义。"""
    if not isinstance(term, dict):
        return "?"
    metric = str(term.get("metric") or "?")
    label = METRIC_LABELS.get(metric, metric)
    code = term.get("code")
    head = f"{label}({code})" if code else label
    return f"{head} {term.get('op') or '?'} {_fmt_val(term.get('value'))}"


def _cond_text(cond: Any) -> str:
    """条件组 → 人话（`A 且 B` / `A 或 B`）。terms 为空 → 空串。"""
    if not isinstance(cond, dict):
        return ""
    terms = [t for t in (cond.get("terms") or []) if isinstance(t, dict)]
    if not terms:
        return ""
    joiner = " 且 " if str(cond.get("logic") or "AND").upper() == "AND" else " 或 "
    return joiner.join(_term_text(t) for t in terms)


def _trigger_text(values: Any) -> str:
    """触发值原样渲染：`标签(代码)=值（阈值 算子 阈值）`。raw 也一并透出。"""
    if not isinstance(values, list) or not values:
        return ""
    parts = []
    for it in values:
        if not isinstance(it, dict):
            continue
        metric = str(it.get("metric") or "?")
        label = METRIC_LABELS.get(metric, metric)
        code = it.get("code")
        head = f"{label}({code})" if code else label
        parts.append(f"{head}={_fmt_val(it.get('value'))}"
                     f"（阈值 {it.get('op') or '?'} {_fmt_val(it.get('threshold'))}）")
    return "、".join(parts)


# ---------------------------------------------------------------- 三个数据源

def load_plan(date8: str) -> Dict[str, Any]:
    """当日计划：present / reason / 条目（各自今日命中次数由 `build` 补）。"""
    file_name = PLAN_TPL.format(date8=date8)
    base = {"present": False, "reason": "not_generated", "file": file_name,
            "trade_date": None, "generated_at": None, "source": None,
            "count": 0, "items": [], "latest_plan_date": None}
    text = _read_text(os.path.join(PANEL_DIR, file_name))
    if text is None:
        return base
    try:
        doc = json.loads(text)
    except (TypeError, ValueError):
        base["reason"] = "unreadable"
        return base
    if not isinstance(doc, dict):
        base["reason"] = "unreadable"
        return base
    meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
    trade_date = str(meta.get("trade_date") or "")
    base["trade_date"] = trade_date or None
    base["generated_at"] = meta.get("generated_at")
    base["source"] = meta.get("source")
    if trade_date != _iso(date8):
        # 文件名是今天的、里面不是今天（手工拷错/补跑写错）→ 明确拒收，不当今天展示
        base["reason"] = "trade_date_mismatch"
        return base
    items: List[Dict[str, Any]] = []
    for p in (doc.get("plans") or []):
        if not isinstance(p, dict):
            continue
        cond = p.get("condition")
        hyst = p.get("hysteresis_exit")
        items.append({
            "dp_id": str(p.get("dp_id") or "?"),
            "theme": str(p.get("theme") or ""),
            "severity": str(p.get("severity") or ""),
            "window": str(p.get("window") or ""),
            "confirm_points": p.get("confirm_points"),
            "condition_text": _cond_text(cond),
            "condition": cond,
            "require_cross_down": bool(p.get("require_cross_down")),
            "hysteresis_text": _cond_text(hyst),
            "hysteresis_exit": hyst,
            "runbook": p.get("runbook") if isinstance(p.get("runbook"), dict) else {},
            "hits": 0,
            "last_hit_ts": None,
            "last_trigger_values": [],
            "last_trigger_text": "",
        })
    return {"present": True, "reason": None, "file": file_name,
            "trade_date": trade_date, "generated_at": meta.get("generated_at"),
            "source": meta.get("source"), "count": len(items), "items": items,
            "latest_plan_date": None}


def latest_plan_date(date8: str) -> Optional[str]:
    """今天缺计划时回溯找「最近一份」——**只作提示，不当今天的数据展示**。"""
    try:
        base = datetime.strptime(date8, "%Y%m%d")
    except ValueError:
        return None
    for back in range(1, LATEST_PLAN_PROBE_DAYS + 1):
        d = (base - timedelta(days=back)).strftime("%Y%m%d")
        if os.path.isfile(os.path.join(PANEL_DIR, PLAN_TPL.format(date8=d))):
            return _iso(d)
    return None


def load_events(trade_date_iso: str) -> Dict[str, Any]:
    """接口流水：只留 `trade_date` == 今天的行，按 ts 升序。损坏行跳过（追加写可能读到半行）。"""
    rows: List[Dict[str, Any]] = []
    bad_lines = 0
    for line in (_read_text(EVENTS_FILE) or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except (TypeError, ValueError):
            bad_lines += 1
            continue
        if not isinstance(ev, dict):
            bad_lines += 1
            continue
        if str(ev.get("trade_date") or "") != trade_date_iso:
            continue
        rows.append(ev)
    rows.sort(key=lambda e: str(e.get("ts") or ""))
    timeline = [{
        "ts": str(ev.get("ts") or ""),
        "dp_id": str(ev.get("dp_id") or ""),
        "theme": str(ev.get("theme") or ""),
        "severity": str(ev.get("severity") or ""),
        "confirm_points": ev.get("confirm_points"),
        "trigger_values": ev.get("trigger_values") or [],
        "trigger_text": _trigger_text(ev.get("trigger_values")),
    } for ev in rows]
    return {"count": len(timeline), "timeline": timeline, "bad_lines": bad_lines}


def load_evaluator(now: datetime, plan_file: str) -> Dict[str, Any]:
    """评估器心跳：状态原样 + 心跳年龄。缺文件/坏文件 → UNKNOWN（不猜在跑）。"""
    out: Dict[str, Any] = {
        "state": "UNKNOWN", "raw_status": None, "pid": None,
        "last_heartbeat": None, "heartbeat_age_s": None, "stale": False,
        "plan_path": None, "plan_path_matches_today": None, "note": None,
    }
    doc = _read_json(STATUS_FILE)
    if doc is None:
        out["note"] = "status_file_missing_or_unreadable"
        return out
    raw = str(doc.get("status") or "").upper()
    out["raw_status"] = raw or None
    out["pid"] = doc.get("pid")
    out["last_heartbeat"] = doc.get("last_heartbeat")
    out["heartbeat_age_s"] = _age_s(doc.get("last_heartbeat"), now)
    out["plan_path"] = doc.get("plan_path")
    if out["plan_path"]:
        name = str(out["plan_path"]).replace("\\", "/").rsplit("/", 1)[-1]
        out["plan_path_matches_today"] = (name == plan_file)
    if raw == "RUNNING":
        out["state"] = "RUNNING"
        age = out["heartbeat_age_s"]
        out["stale"] = age is not None and age > HEARTBEAT_STALE_S
    elif raw == "EXITED":
        out["state"] = "EXITED"
    else:
        out["note"] = "status_unknown_value"
    return out


def switch_state() -> Dict[str, Any]:
    """唤醒总开关状态（唯一实现 = `engine/common/wake_switch.py`）；不可用 → 按开并标异常。"""
    try:
        from common.wake_switch import read_switch  # noqa: PLC0415 - 引擎侧唯一实现
        return read_switch()
    except Exception:  # noqa: BLE001 - 开关模块不可用 = 按开启处理，绝不让卡片打不开
        return {"enabled": True, "ok": False, "reason": "module_unavailable_default_on",
                "updated_at": None, "by": None, "path": None}


# ---------------------------------------------------------------- 装配

def build(now: Optional[datetime] = None, date8: Optional[str] = None) -> Dict[str, Any]:
    """决策点分区的完整载荷（收 now/date8 便于测试注入）。"""
    now = now or datetime.now()
    date8 = date8 or _date8(now)
    iso = _iso(date8)

    plan = load_plan(date8)
    if not plan["present"]:
        plan["latest_plan_date"] = latest_plan_date(date8)

    hits = load_events(iso)

    # 每条计划挂上今日命中次数 / 最后触发（只数流水，不判定）
    by_dp: Dict[str, List[Dict[str, Any]]] = {}
    for row in hits["timeline"]:
        by_dp.setdefault(row["dp_id"], []).append(row)
    for item in plan["items"]:
        rows = by_dp.get(item["dp_id"]) or []
        item["hits"] = len(rows)
        if rows:
            last = rows[-1]
            item["last_hit_ts"] = last["ts"]
            item["last_trigger_values"] = last["trigger_values"]
            item["last_trigger_text"] = last["trigger_text"]

    # 流水里有、今日计划里没有的 dp（计划白天重渲过/长期预警跑了别的 dp）→ 如实列出
    plan_ids = {item["dp_id"] for item in plan["items"]}
    unplanned = []
    for dp_id, rows in by_dp.items():
        if dp_id in plan_ids:
            continue
        unplanned.append({
            "dp_id": dp_id,
            "theme": rows[-1]["theme"],
            "count": len(rows),
            "last_ts": rows[-1]["ts"],
            "last_trigger_text": rows[-1]["trigger_text"],
        })
    unplanned.sort(key=lambda r: str(r.get("last_ts") or ""))

    return {
        "ok": True,
        "now": now.strftime("%Y-%m-%d %H:%M:%S"),
        "date": iso,
        "date8": date8,
        "is_today": date8 == _date8(now),
        "plan": plan,
        "hits": {**hits, "unplanned": unplanned},
        "evaluator": load_evaluator(now, plan["file"]),
        "switch": switch_state(),
        "caliber": {
            "readonly": True,
            "no_recompute": "命中次数/触发值取 wake_events.jsonl 原值，不重算条件",
            "plan_items": "计划条目原样展示，不标已满足/未满足",
            "heartbeat_stale_s": HEARTBEAT_STALE_S,
            "switch_scope": "关 = 不叫醒夏天（不调 wake_agent）；评估与事件落盘照常，alert 推送不受影响",
            "sources": {
                "plan": "data/panel/wake_plan_{YYYYMMDD}.json（build_wake_plan.py）",
                "events": "engine/state/wake_events.jsonl（wake_eval.py 追加）",
                "status": "engine/state/wake_eval_status.json（60s 心跳）",
                "switch": "engine/state/wake_switch.json（common/wake_switch.py）",
            },
        },
    }
