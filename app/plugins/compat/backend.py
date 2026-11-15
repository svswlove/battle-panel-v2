# -*- coding: utf-8 -*-
"""compat 插件：旧 8800 端点兼容层（第 1 批 · 只读）。

**为什么单独一个插件**：旧前端（`engine/web/*`）调 **85 个 `/api/...`** 路径。过渡期让旧界面
直接跑在新后端 8810 上，需要一批「同名、同响应格式」的端点。本插件专载这批兼容端点，
不 import 其他插件（`sector/kline/amv/...` 一个都不用），直接读同一份数据源。

**安全边界（硬约束）**
- 只读：不签收、不下单、不改单、不急停、不写任何生产文件；
  （2026-10-24 批次 0 新增的唯一 POST `/api/shell/search` 也只是读：同源直调 `build_search`。
   急停/签收走 core 的 `/api/shell/kill`、`/api/sign`，本插件不承载、不代理——见 docs/HANDOFF_20261024_fe_batch0.md）
- 数据只走 `ctx.panel_json()` / `ctx.json_read()` / `ctx.db_read()` / `ctx.quotes`；
- 缺数据一律 `null` / `[]` / 明确错误，**不填 0、不猜、不拿旧文件冒充当日**。

**格式唯一依据**：逐行对齐旧仓 `engine/server.py`（只读参考，未改一行）——
`/api/{name}.json` 白名单透传、`api_status`、`build_gates_payload`、`build_alerts_payload`、
`api_real_positions`、`api_search_all`、`api_push_types`。字段名、嵌套、数组元素键名逐字照搬。

**本批与任务书假设不符的三处（已在 docs/compat_batch1.md 记录）**
1. `/api/dashboard.json`：任务书说「不是单文件，要组装」，但旧端 `WHITELIST` 含 `"dashboard"`，
   实际就是 `_serve_doc("dashboard")` 直读 `data/panel/dashboard.json`。**原样透传即逐字段一致**，
   重新组装只会引入漂移，故透传。
2. `/api/push_types.json`：旧端不是文件端点，是常量端点（`push_queue` 枚举 + 中文标签）。
   磁盘无 `data/panel/push_types.json`，只能内置快照（见下方 `_PUSH_TYPE_*`），有漂移风险，
   已标注定期对齐。
3. `/api/concept.json`：本批**曾**返回 503 骨架（数据源是 `夏天-activity/概念追踪/` 的
   Markdown 知识库，当时不在任何只读白名单）。**2026-10-23 已移交**独立 `concept` 插件：
   core 新增 `data.text` / `ctx.text_dir()` 目录级只读白名单后，该端点由
   `app/plugins/concept/` 以 root 挂载实现，本插件的路由已删除（避免 root 路径冲突）。

**0.3.0（2026-10-24 R5）**：`/api/status` 的 `trade_mode` 不再恒为 null。core 的
`ctx.state_json(name)`（N2 落地，读 `paths.WRITABLE_STATE` 同一份白名单）让插件能只读
`engine/state/engine_state.json` 与 `kill_switch.json`；本插件在 `manifest.data.state`
声明这两个名字，`build_status` 据此补齐 `trade_mode`/`kill_switch_active`/`kill_reason`，
与 8800 同参逐字段一致。**仍不写任何文件**（`data.write` 保持空数组）。
"""
from __future__ import annotations

import contextlib
import os
import re
import threading
import time
import unicodedata
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from fastapi import APIRouter, HTTPException, Query
from fastapi.routing import APIRoute
from starlette.routing import Match

from app.core import paths as core_paths

# ---------------------------------------------------------------- 版本与阈值
# 全部沿用旧端同名常量（engine/server.py），改口径必须先改旧端。
_SHELL_VERSION = datetime.now().strftime("%Y%m%d%H%M%S")
GATES_VERSION = "v1-20260914"
ALERTS_VERSION = "v1-20260912"

GATE_EMOTION_LIMIT_UP_RED = 30        # 情绪门 RED：涨停 < 30（退潮加速）
GATE_EMOTION_LIMIT_DOWN_RED = 25      # 情绪门 RED：跌停 > 25
GATE_EMOTION_LIMIT_UP_GREEN = 70      # 情绪门 GREEN：涨停 ≥ 70
GATE_EMOTION_LIMIT_DOWN_GREEN = 15    # 情绪门 GREEN：跌停 ≤ 15
GATE_APPROACH_PCT = 1.0               # 持仓门黄档：距止损线 ≤1%
GATE_DATA_STALE_SECONDS = 900         # data_asof 过期阈值（15 分钟）

# engine/state 只读状态文件名（0.3.0）：旧 8800 `/api/status` 的 `trade_mode` 与
# `kill_switch_active`/`kill_reason` 直接来自这两个文件。core 的 `ctx.state_json(name)`
# （N2 落地）读的就是 `paths.WRITABLE_STATE` 同一份白名单，插件侧必须在
# `manifest.data.state` 声明才拿得到（未声明 → PermissionError）。
STATE_ENGINE = "engine_state"
STATE_KILL = "kill_switch"

ALERT_NEAR_PCT = 1.0                  # 预案触发价黄档：距线 <1%
ALERT_PNL_STOP_YELLOW_PCT = 10.0      # 持仓止损距离黄档：<10%
ALERT_PNL_STOP_RED_PCT = 5.0          # 持仓止损距离红档：<5%
ALERT_STALE_SECONDS = 900             # 数据源新鲜度阈值
ALERT_FUTURE_TOLERANCE_SECONDS = 60   # 时间戳未来容差

STATE_ORDER = {"RED": 3, "YELLOW": 2, "GREEN": 1, "UNKNOWN": 0}

LABEL_STALE_TRADING_DAYS = 1          # 标签源（positions.json）陈旧阈值

SEARCH_LIMIT_DEFAULT = 20
SEARCH_LIMIT_MAX = 50

# ---------------------------------------------------------------- 推送类型快照
# 旧端 `/api/push_types.json` 是**常量端点**，不是文件端点：
#   types        = engine/common/push_queue.py::NOTIFY_TYPE_KEYS（9 类，写 battle_plan.notify_types）
#   labels       = engine/server.py::PUSH_TYPE_LABELS（中文标签唯一映射）
#   all_whitelist= engine/common/push_queue.py::EVENT_WHITELIST（12 类，含系统级事件）
# 这 3 份常量在旧仓，panel-v2 侧无只读通道可读（READABLE_* 白名单不含 py 常量），
# 故内置快照。**旧仓改枚举时这里必须同步**；对不齐会在 compat_batch1.md 的比对里暴露。
_PUSH_TYPE_KEYS = (
    "ORDER_FILLED", "ORDER_PARTIAL", "ORDER_CANCELLED", "ORDER_REJECTED",
    "SIGNAL_TRIGGERED", "STOP_TRIGGERED", "TAKE_PROFIT_TRIGGERED",
    "DO_T_TRIGGERED", "MODULE_OFF",
)
_PUSH_TYPE_LABELS = {
    "ORDER_FILLED": "买入成交",
    "ORDER_PARTIAL": "部分成交",
    "ORDER_CANCELLED": "撤单",
    "ORDER_REJECTED": "废单",
    "SIGNAL_TRIGGERED": "信号/择机触发",
    "STOP_TRIGGERED": "止损触发",
    "TAKE_PROFIT_TRIGGERED": "止盈触发",
    "DO_T_TRIGGERED": "做T触发",
    "MODULE_OFF": "模块关闭/降级",
    "MODULE_ON": "模块启用",
    "BATCH_CAP_CONFLICT": "批次超单笔上限",
}
_PUSH_EVENT_WHITELIST = (
    "ORDER_FILLED", "ORDER_PARTIAL", "ORDER_CANCELLED", "ORDER_REJECTED",
    "SIGNAL_TRIGGERED", "STOP_TRIGGERED", "TAKE_PROFIT_TRIGGERED",
    "DO_T_TRIGGERED", "MODULE_OFF", "MODULE_ON", "BATCH_CAP_CONFLICT",
    "CHAIN_STEP_FAILED",
)
_PUSH_CHANNEL = {
    "mode": "instant",
    "transport": "qq_bot_api",
    "queue_file": "state/push_queue.jsonl",
    "note": "事件发生即推 QQ（直连官方 API，非轮询排队）；notify 关=仅落盘不推；"
            "落盘队列供审计与失败尾注，不参与投递时序",
    "levels": {"error": "严重告警无条件推（旁路类型白名单）",
               "warn": "受类型白名单约束", "info": "受类型白名单约束"},
}

# 信号来源最小中文映射（旧端 cnSigSource 同表）
_SIG_SOURCE_CN = {"POOL_OVERLAP": "池内买点", "FIVE_DAY": "五日线", "RESONANCE": "共振",
                  "B1B2B3": "通达信选股", "COMMAND_B": "指令B", "MASTER_DIRECT": "主人直指"}

# ---------------------------------------------------------------- 通配 {name}.json
# 旧端 `/api/{name}.json` 的只读等价（engine/server.py::api_doc 的 `WHITELIST` 分支）。
# 白名单 = **core 的 READABLE_PANEL**（唯一源，本插件不自建第二份名单）；不在其中 → 404。
# 防任意文件读：名字只能以白名单键命中，插件不做任何路径拼接。
_PANEL_JSON_NAMES = frozenset(core_paths.READABLE_PANEL)

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


def _parse_ts(raw: Any) -> Optional[datetime]:
    """面板 JSON 时间戳（'YYYY-MM-DD HH:MM:SS'）→ datetime；不可解析 → None（不猜新鲜）。"""
    if not raw or not isinstance(raw, str):
        return None
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            return datetime.fromisoformat(raw.strip())
        except ValueError:
            return None


def _worst(states: Sequence[str]) -> str:
    """多档聚合取最差（RED>YELLOW>GREEN>UNKNOWN；UNKNOWN 不遮蔽告警）。"""
    if not states:
        return "UNKNOWN"
    return max(states, key=lambda s: STATE_ORDER.get(s, 0))


def _num(value: Any, digits: int = 2) -> str:
    """告警文案数字格式化（None/非法 → '—'；与旧端 num() 同式）。"""
    if not _finite(value):
        return "—"
    return f"{float(value):.{digits}f}"


# ---------------------------------------------------------------- 只读取数
def _panel(ctx: Any, name: str) -> Optional[Dict[str, Any]]:
    """读 panel JSON；缺失/不可读 → None（调用方决定 404 还是降级，不抛穿）。"""
    getter = getattr(ctx, "panel_json", None)
    if not callable(getter):
        return None
    try:
        data = getter(name)
    except Exception:  # noqa: BLE001 - 缺文件/越权一律按「不可得」处理
        return None
    return data if isinstance(data, dict) else None


def _state(ctx: Any, name: str) -> Optional[Dict[str, Any]]:
    """读一个引擎状态文件；缺通道/缺文件/坏文件 → None（调用方决定降级口径）。

    只走 `ctx.state_json`（core 正式通道，白名单在 core，本插件不碰路径）。
    老 core 没有该方法时返回 None，**不**退到直读文件——插件读路径一律经 ctx。
    """
    getter = getattr(ctx, "state_json", None)
    if not callable(getter):
        return None
    try:
        data = getter(name)
    except Exception:  # noqa: BLE001 - 缺文件/越权/坏 JSON 一律按「不可得」处理
        return None
    return data if isinstance(data, dict) else None


def _panel_or_404(ctx: Any, name: str) -> Dict[str, Any]:
    """透传类端点：文件缺失 → 404 + detail（任务书 §2.1 口径）。"""
    getter = getattr(ctx, "panel_json", None)
    if not callable(getter):
        raise HTTPException(status_code=500, detail="ctx.panel_json 不可用")
    try:
        return getter(name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="%s.json 不存在：%s" % (name, exc))
    except PermissionError as exc:
        raise HTTPException(status_code=500, detail="%s 未在插件清单声明：%s" % (name, exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail="%s 读取失败：%s" % (name, exc))


def _name_index(ctx: Any) -> Dict[str, str]:
    """代码→名称（`ctx.json_read("stock_names")` 的 `names` 段：6 位码→名称）。"""
    reader = getattr(ctx, "json_read", None)
    if not callable(reader):
        return {}
    try:
        data = reader("stock_names")
    except Exception:  # noqa: BLE001 - 名表不可用不阻断持仓展示
        return {}
    names = data.get("names") if isinstance(data, dict) else None
    return names if isinstance(names, dict) else {}


def _code_hit(token: str, code6: str, full: str) -> bool:
    """代码命中口径（2026-10-28 主人定）：纯数字**至少 3 位**且只按**后 N 位**（代码尾部）匹配。

    · `519` / `0519` → 只命中尾部为这几位数的票（600519、000519…），不再命中 601519 以外的“中间段”；
    · `1` / `51` → 不参与代码匹配（避免大量噪声），只走名称/拼音；
    · 含字母或点（`600519.SH`、`sh600519`）→ 全码子串匹配。
    """
    if not token:
        return False
    if token.isdigit():
        return len(token) >= 3 and code6.endswith(token)
    return token in full


def _text_hit(token: str, low: str, pf: str, pi: str) -> bool:
    """名称/拼音命中口径（2026-10-28 主人定）：**至少 2 个字符**，单字/单字母不搜。

    · `茅` / `m` → 不命中（范围太宽、噪声大）；`茅台` / `mt` → 命中；
    · 纯数字 token 只走代码（见 `_code_hit`），不拿数字去碰名称/拼音。
    """
    if len(token) < 2 or token.isdigit():
        return False
    return (token in low) or (token in pf) or (token in pi)


def _norm_text(s: Any) -> str:
    """搜索归一：NFKC（全角→半角）+ 去掉所有空白 + 小写。

    2026-10-28 主人报「万科」搜不到：「万 科Ａ」名称中间带空格，子串匹配失败。
    归一后再比，名称/代码/拼音三路都用同一口径。
    """
    txt = unicodedata.normalize("NFKC", str(s or ""))
    return re.sub(r"\s+", "", txt).lower()


def _pinyin_map(ctx: Any) -> Dict[str, Dict[str, str]]:
    """名称 → {f: 全拼, i: 首字母}（`ctx.json_read("pinyin_index")`，构建期生成、运行时只读）。

    索引由 `tools/build_pinyin_index.py` 产出（pypinyin 只在构建期用，不装进运行环境）；
    文件缺失/不可读 → 空表，搜索退化为代码+名称，不报错。
    """
    reader = getattr(ctx, "json_read", None)
    if not callable(reader):
        return {}
    try:
        data = reader("pinyin_index")
    except Exception:  # noqa: BLE001 - 索引不可用不阻断搜索
        return {}
    items = data.get("items") if isinstance(data, dict) else None
    return items if isinstance(items, dict) else {}


def _name_of(raw_name: Any, code: Any, name_index: Dict[str, str]) -> str:
    """名称统一出口：显式名 → 名表 → 代码（**真查不到才回退代码**，与旧端同序）。"""
    if raw_name:
        return str(raw_name)
    key = str(code or "")
    hit = name_index.get(key) or name_index.get(key.split(".")[0])
    if hit:
        return str(hit)
    return key


def _prev_close(quote: Dict[str, Any]) -> Optional[float]:
    """由 现价 + 涨跌幅 反推昨收；缺一不可 → None（与旧端 `_prev_close` 同式）。"""
    price, chg = quote.get("price"), quote.get("change_pct")
    if not _finite(price) or not _finite(chg) or (1 + float(chg) / 100.0) == 0:
        return None
    return round(float(price) / (1 + float(chg) / 100.0), 4)


def _quotes_for(ctx: Any, codes: Sequence[Any]) -> Dict[str, Dict[str, Any]]:
    """批量实时价（只读）：→ {ts_code | code6: {price, change_pct, open, prev_close, asof, source}}。

    档位与旧端 `_live_quotes_for` 一致：
      ① `watchlist_quotes.json`（tick_feed 每轮全市场快照，零新连接）；
      ② `ctx.quotes`（eltdx 只读行情门面）补未命中票——不可用即静默降级，不编造。
    双键索引（带后缀 + 6 位码），调用方任一形态都能命中。
    """
    out: Dict[str, Dict[str, Any]] = {}
    want = {str(c) for c in (codes or []) if c}
    if not want:
        return out
    want |= {c.split(".")[0] for c in want}

    wq = _panel(ctx, "watchlist_quotes") or {}
    asof = wq.get("asof")
    for code, rec in (wq.get("quotes") or {}).items():
        if not isinstance(rec, dict):
            continue
        if str(code) not in want and str(code).split(".")[0] not in want:
            continue
        if not _finite(rec.get("price")):
            continue
        item = {"price": float(rec["price"]), "change_pct": rec.get("change_pct"),
                "open": rec.get("open"), "prev_close": _prev_close(rec),
                "asof": asof, "source": "watchlist_quotes"}
        out[str(code)] = item
        out[str(code).split(".")[0]] = item

    misses = [c for c in (codes or []) if c and str(c) not in out
              and str(c).split(".")[0] not in out]
    try:
        quotes_facade = getattr(ctx, "quotes", None)
    except Exception:  # noqa: BLE001 - 未声明 quotes 权限/门面不可用 → 只走 watchlist_quotes
        quotes_facade = None
    if misses and quotes_facade is not None:
        for code in misses:
            try:
                glb = quotes_facade.normalize(str(code))
                rec = quotes_facade.depth(glb) if glb else None
            except Exception:  # noqa: BLE001 - 行情源不可达 → 保持降级
                rec = None
            if rec is None:
                continue
            try:
                price = float(getattr(rec, "last_price", 0) or 0)
                pre = float(getattr(rec, "last_close_price", 0) or 0)
                open_px = float(getattr(rec, "open_price", 0) or 0)
            except (TypeError, ValueError):
                continue
            if not _finite(price) or price <= 0:
                continue
            item = {"price": round(price, 3),
                    "change_pct": round((price - pre) / pre * 100, 4) if (_finite(pre) and pre) else None,
                    "open": open_px if _finite(open_px) else None,
                    "prev_close": round(pre, 3) if (_finite(pre) and pre) else None,
                    "asof": _now(), "source": "eltdx:snapshot"}
            out[str(code)] = item
            out[str(code).split(".")[0]] = item
    return out


def _quote_of(quotes: Dict[str, Dict[str, Any]], code: Any) -> Dict[str, Any]:
    """按 ts_code 或 6 位码取值；都无 → 空 dict（调用方按缺价处理）。"""
    key = str(code or "")
    return quotes.get(key) or quotes.get(key.split(".")[0]) or {}


# ---------------------------------------------------------------- 持仓口径统一
def _label_stale(positions: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """positions.json（标签源）时效标注——交易日粒度。

    空 holdings **不算陈旧**（旧端 2026-10-22 同口径：无标签可陈旧 → state=empty，
    避免「陈旧 24 天」这种永远不会转绿的假警报）。
    """
    if not positions:
        return {"state": "missing", "age_seconds": None, "updated_at": None}
    ts = _parse_ts(positions.get("updated_at")) or _parse_ts(
        (positions.get("meta") or {}).get("generated_at"))
    if ts is None:
        return {"state": "unknown", "age_seconds": None, "updated_at": None}
    age_s = (datetime.now() - ts).total_seconds()
    age_days = age_s / 86400.0
    if not (positions.get("holdings") or []):
        return {"state": "empty", "age_seconds": int(age_s), "age_days": round(age_days, 2),
                "updated_at": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "note": "标签源无持仓（holdings 为空）→ 无预案标签可陈旧"}
    state = "stale" if age_days >= LABEL_STALE_TRADING_DAYS else "fresh"
    return {"state": state, "age_seconds": int(age_s),
            "age_days": round(age_days, 2), "updated_at": ts.strftime("%Y-%m-%d %H:%M:%S")}


def _trade_mode(ctx: Any) -> str:
    """当前交易模式（`engine_state.json::trade_mode`，旧端同语义）。

    合法值只有 `live` / `paper`；文件缺失、读不到、值非法 → `live`（旧端
    `mode = trade_mode if trade_mode in ("live", "paper") else "live"` 同款保守默认）。
    只读：走 `ctx.state_json`（契约 §4.5），本函数不写任何文件。
    """
    doc = _state(ctx, STATE_ENGINE) or {}
    mode = str(doc.get("trade_mode") or "")
    return mode if mode in ("live", "paper") else "live"


def _paper_holdings_view(ctx: Any, positions: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """纸面模式的持仓权威源（旧端 `resolve_holdings` 的 paper 分支）。

    旧端读 `engine/state/paper_account.json`；8810 的纸面账本已经搬到 `app/core/paper/`
    （4.1.3b），对外由 `paper` 插件的 `/api/paper_account` 暴露，所以这里**复用同一个
    口子**（`ctx.paper`）而不另建一条读文件的路径 —— 两个端点对同一份账本各说一套
    才是真正的口径分裂。注意：纸面账本是**新账本**（`data/panel_v2/paper/`），
    与旧 8800 的 `engine/state/paper_account.json` 不是同一份数据。

    `source_updated_at` 给 `updated_at`（供 data_asof），`source_trade_date` 给
    `trade_date`（旧端用它判 stale：账本只在成交时写，`updated_at` 不是新鲜度信号）。
    取不到账本 → 空 items + `source_missing=True`（不编造、不回落实盘）。
    """
    name_index = _name_index(ctx)
    doc: Dict[str, Any] = {}
    trade_date: Optional[str] = None
    missing = True
    try:
        svc = ctx.paper                       # 未声明 data.paper → PermissionError
        acct = svc.account_view()
        if isinstance(acct, dict):
            doc = acct
            missing = False
        # trade_date 只在账本本体里（`account()`），`account_view()` 是精简视图不带它。
        # 旧端 paper_account.json 把 trade_date 写在顶层，持仓门的 stale 判据就靠它 ——
        # 这里补取一次，否则 data_basis 永远退到 updated_at，与 8800 不一致。
        raw_acct = svc.account()
        if isinstance(raw_acct, dict):
            trade_date = raw_acct.get("trade_date")
            doc.setdefault("updated_at", raw_acct.get("updated_at"))
    except Exception:  # noqa: BLE001 - 账本不可读 → 按缺失处理，不阻断持仓门
        doc = {}
    raw = doc.get("positions") or {}
    items: List[Dict[str, Any]] = []
    if isinstance(raw, dict):
        pairs = raw.items()
    elif isinstance(raw, list):
        pairs = ((p.get("ts_code") or p.get("code"), p) for p in raw if isinstance(p, dict))
    else:
        pairs = ()
    for code, payload in pairs:
        if not code or not isinstance(payload, dict):
            continue
        qty = payload.get("qty")
        if not _finite(qty) or float(qty or 0) <= 0:
            continue
        items.append({"ts_code": str(code), "name": payload.get("name"),
                      "qty": int(qty), "cost_price": float(payload.get("cost_price") or 0),
                      "plan": None, "exit_watch": None})
    labels: Dict[str, Dict[str, Any]] = {}
    for holding in ((positions or {}).get("holdings") or []):
        code = holding.get("ts_code")
        if code:
            labels[str(code)] = {"plan": holding.get("plan"),
                                 "exit_watch": holding.get("exit_watch"),
                                 "name": holding.get("name")}
    for item in items:
        label = labels.get(item["ts_code"])
        if label:
            item["plan"] = label.get("plan")
            item["exit_watch"] = label.get("exit_watch")
            item["name"] = item.get("name") or label.get("name")
        item["name"] = _name_of(item.get("name"), item["ts_code"], name_index)
    return {"trade_mode": "paper", "source": "paper_account.json",
            "source_file": "paper_account",
            "source_updated_at": doc.get("updated_at"),
            "source_trade_date": trade_date,
            "source_missing": missing, "items": items,
            "labels_available": bool(labels), "stale": _label_stale(positions)}


def _resolve_holdings(ctx: Any, positions: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """持仓标的 + positions.json 标签关联（旧端 `resolve_holdings` 只读复刻）。

    **trade_mode**（2026-10-24 01 修）：旧端读 `engine/state/engine_state.json` 的 `trade_mode`，
    合法值 `live`/`paper`，非法或缺失 → `live`（旧端同款保守默认）。
    原先这里写死 `mode = "live"`，理由是「engine_state.json 不在只读白名单」——
    那个前提已经过时：N2 批次落地了 `ctx.state_json()`（契约 §4.5），本插件 manifest
    已声明 `data.state: ["engine_state", "kill_switch"]`，`build_status` 一直在用。
    写死的后果：`trade_mode=paper` 时持仓门仍读 `real_positions.json`（实盘），
    与旧 8800 的持仓来源、data_basis、data_stale 三处判据全部不一致。
    """
    name_index = _name_index(ctx)
    mode = _trade_mode(ctx)
    if mode == "paper":
        # 纸面模式：权威源换成纸面账本（旧端 `STATE_PAPER_ACCOUNT`）
        return _paper_holdings_view(ctx, positions)
    rp = _panel(ctx, "real_positions")   # None = 文件缺失（与 {} 区分，旧端同口径）
    src_doc = rp or {}
    items: List[Dict[str, Any]] = []
    for row in (src_doc.get("positions") or []):
        if not isinstance(row, dict):
            continue
        if not _finite(row.get("qty")) or float(row.get("qty") or 0) <= 0:
            continue
        code = str(row.get("code") or "")
        if not code:
            continue
        items.append({"ts_code": code, "name": row.get("name"),
                      "qty": int(row.get("qty") or 0),
                      "cost_price": float(row.get("cost_price") or 0),
                      "plan": None, "exit_watch": None})
    source, source_file = "real_positions.json", "real_positions"
    source_missing = rp is None

    labels: Dict[str, Dict[str, Any]] = {}
    for holding in ((positions or {}).get("holdings") or []):
        code = holding.get("ts_code")
        if code:
            labels[str(code)] = {"plan": holding.get("plan"),
                                 "exit_watch": holding.get("exit_watch"),
                                 "name": holding.get("name")}
    for item in items:
        label = labels.get(item["ts_code"])
        if label:
            item["plan"] = label.get("plan")
            item["exit_watch"] = label.get("exit_watch")
            item["name"] = item.get("name") or label.get("name")
        item["name"] = _name_of(item.get("name"), item["ts_code"], name_index)
    return {"trade_mode": mode, "source": source, "source_file": source_file,
            "source_updated_at": src_doc.get("updated_at"),
            "source_trade_date": src_doc.get("trade_date"),
            "source_missing": source_missing, "items": items,
            "labels_available": bool(labels), "stale": _label_stale(positions)}


def _holdings_view_rows(pairs: Sequence[Any], quotes: Dict[str, Dict[str, Any]],
                        name_index: Dict[str, str]) -> tuple:
    """统一持仓明细（旧端 `_build_holdings_view` 同列同序）。

    列：代码+名称｜数量｜可卖｜成本｜现价｜盈亏(额)｜盈亏%｜当日收益(额)｜当日盈亏比%
        ｜市值｜仓位占比%｜建仓时间。现价缺失 → 回退成本价并标 `price_source=cost_fallback`
    （**不编造行情**）。
    """
    rows: List[Dict[str, Any]] = []
    mv_total = 0.0
    for code, payload in pairs:
        qty = int(payload.get("qty") or 0)
        avail = payload.get("available_qty")
        avail = int(avail) if avail is not None else qty
        cost = float(payload.get("cost_price") or 0)
        quote = _quote_of(quotes, code)
        has_live = _finite(quote.get("price")) and float(quote.get("price") or 0) > 0
        last = float(quote["price"]) if has_live else cost
        mv = round(qty * last, 2)
        pnl_amt = round((last - cost) * qty, 2)
        pnl_pct = round((last / cost - 1) * 100, 4) if cost else 0.0
        prev = quote.get("prev_close")
        day_amt = day_pct = None
        if has_live and _finite(prev) and float(prev) > 0:
            day_amt = round((last - float(prev)) * qty, 2)
            day_pct = round((last / float(prev) - 1) * 100, 4)
        mv_total += mv
        rows.append({
            "ts_code": code,
            "name": _name_of(payload.get("name"), code, name_index),
            "qty": qty, "available_qty": avail,
            "cost_price": round(cost, 3), "last_price": round(last, 3),
            "price_source": quote.get("source") if has_live else "cost_fallback",
            "quote_asof": quote.get("asof") if has_live else None,
            "pnl_amount": pnl_amt, "pnl_pct": pnl_pct,
            "day_pnl_amount": day_amt, "day_pnl_pct": day_pct,
            "market_value": mv, "weight_pct": None,
            "opened_at": payload.get("opened_at") or payload.get("created_at")
            or payload.get("open_date"),
        })
    for row in rows:
        row["weight_pct"] = round(row["market_value"] / mv_total * 100, 2) if mv_total > 0 else None
    return rows, {"market_value": round(mv_total, 2)}


# ---------------------------------------------------------------- 三门评估
def _data_stale(asof: Any, now_dt: Optional[datetime] = None) -> bool:
    """单门 data_asof 过期判定：缺时间戳=True（无从证明新鲜）；>900s=True；未来>60s=True。"""
    ts = _parse_ts(asof)
    if ts is None:
        return True
    age = ((now_dt or datetime.now()) - ts).total_seconds()
    return age > GATE_DATA_STALE_SECONDS or age < -ALERT_FUTURE_TOLERANCE_SECONDS


def _emotion_gate(limit_up: Any, limit_down: Any) -> Dict[str, Any]:
    """情绪门三态（旧端 `evaluate_emotion_gate` 逐字复刻，绝对阈值口径）。"""
    if not (_finite(limit_up) and _finite(limit_down)):
        return {"state": "UNKNOWN",
                "reason": "intraday.json market_breadth 无涨停/跌停数（快照未产出），不编造"}
    lu, ld = int(limit_up), int(limit_down)
    if lu < GATE_EMOTION_LIMIT_UP_RED or ld > GATE_EMOTION_LIMIT_DOWN_RED:
        return {"state": "RED",
                "reason": f"退潮加速：涨停 {lu}<{GATE_EMOTION_LIMIT_UP_RED} 或 跌停 {ld}>{GATE_EMOTION_LIMIT_DOWN_RED}"}
    if lu >= GATE_EMOTION_LIMIT_UP_GREEN and ld <= GATE_EMOTION_LIMIT_DOWN_GREEN:
        return {"state": "GREEN",
                "reason": f"涨停 {lu}≥{GATE_EMOTION_LIMIT_UP_GREEN} 且跌停 {ld}≤{GATE_EMOTION_LIMIT_DOWN_GREEN}（企稳/强势）"}
    return {"state": "YELLOW",
            "reason": f"涨停 {lu}、跌停 {ld} 均未达红/绿档（介于退潮加速与企稳之间）"}


def _holding_gate_item(price: Optional[float], exit_watch: Any) -> tuple:
    """单持仓纪律线三态（旧端 `evaluate_holding_item`）：武装 STOP_LOSS 最低线为判据。"""
    if not (isinstance(exit_watch, dict) and exit_watch.get("active")):
        return None, []
    levels_raw = exit_watch.get("trigger_levels") or []
    stops = [lv for lv in levels_raw
             if lv.get("level_type") == "STOP_LOSS" and lv.get("armed") and _finite(lv.get("price"))]
    if not stops:
        return None, []
    levels = [{"level_type": lv.get("level_type"), "price": lv.get("price"),
               "armed": bool(lv.get("armed")), "note": lv.get("note")} for lv in levels_raw]
    stop = min(float(lv["price"]) for lv in stops)
    if not _finite(price) or float(price) <= 0:
        return "UNKNOWN", levels
    p = float(price)
    if p <= stop:
        return "RED", levels
    if (p - stop) / stop * 100.0 <= GATE_APPROACH_PCT:
        return "YELLOW", levels
    return "GREEN", levels


def _stop_distance_state(price: Any, stop_price: Any) -> str:
    """持仓距止损距离三态（旧端 `evaluate_stop_distance`）。"""
    if not _finite(price) or not _finite(stop_price) or float(stop_price) <= 0 or float(price) <= 0:
        return "UNKNOWN"
    dist = (float(price) - float(stop_price)) / float(stop_price) * 100.0
    if dist < ALERT_PNL_STOP_RED_PCT:
        return "RED"
    if dist < ALERT_PNL_STOP_YELLOW_PCT:
        return "YELLOW"
    return "GREEN"


def _selection_source(ctx: Any) -> Dict[str, Any]:
    """选股门数据源（旧端 `_selection_gate_source`）：`buy_signal.db::stock_signal` 当日产物。

    当日无产物 → `{date: "", ...}` 如实报「未产出」，**不拿停写的 pool_track 兜底**。
    `asof` = 库文件 mtime（经 `PRAGMA database_list` 拿真实路径，只读 stat）。
    """
    rows: List[Any] = []
    asof = ""
    try:
        conn = ctx.db_read("buy_signal.db")
    except Exception:  # noqa: BLE001 - 库不可用 → 如实报未产出
        return {"date": "", "asof": "", "codes": []}
    try:
        rows = list(conn.execute(
            "SELECT stock, daily_date, daily_sigs, weekly_date, weekly_sigs FROM stock_signal"))
        try:
            for _seq, _name, db_file in conn.execute("PRAGMA database_list"):
                if db_file:
                    asof = datetime.fromtimestamp(os.path.getmtime(db_file)).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:  # noqa: BLE001 - mtime 取不到 → 空（与旧端 OSError 同处理）
            asof = ""
    except Exception:  # noqa: BLE001
        rows = []
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    best = ""
    for _s, dd, _ds, wd, _ws in rows:
        for d in (str(dd or ""), str(wd or "")):
            if d > best:
                best = d
    hits: Dict[str, Dict[str, Any]] = {}
    for stock, dd, ds, wd, ws in rows:
        daily = [x.strip() for x in str(ds or "").split(",") if x.strip()]
        weekly = [x.strip() for x in str(ws or "").split(",") if x.strip()]
        if not daily and not weekly:
            continue
        if str(dd or "") != best and str(wd or "") != best:
            continue
        rec = hits.setdefault(str(stock), {"ts_code": str(stock), "name": str(stock), "badges": []})
        for tag in (daily if str(dd or "") == best else []) + (weekly if str(wd or "") == best else []):
            if tag not in rec["badges"]:
                rec["badges"].append(tag)
    if hits:
        name_index = _name_index(ctx)
        for code, rec in hits.items():
            rec["name"] = _name_of(None, code, name_index)
    return {"date": best, "asof": asof, "codes": list(hits.values())}


def build_gates(ctx: Any, quotes_fn=None) -> Dict[str, Any]:
    """三道门（旧端 `build_gates_payload` 逐字段复刻）：`{version,updated_at,gate1,gate2,gate3}`。

    `quotes_fn(codes) → {code: quote}` 可注入（测试零行情 IO）。
    """
    quotes_fn = quotes_fn or (lambda codes: _quotes_for(ctx, codes))
    positions = _panel(ctx, "positions")
    intraday = _panel(ctx, "intraday") or {}
    view = _resolve_holdings(ctx, positions)

    breadth = intraday.get("market_breadth") or {}
    g1_asof = breadth.get("asof")
    g1 = dict(_emotion_gate(breadth.get("limit_up"), breadth.get("limit_down")),
              data_asof=g1_asof, data_stale=_data_stale(g1_asof))

    holdings = view.get("items") or []
    need = [h.get("ts_code") for h in holdings
            if isinstance(h.get("exit_watch"), dict) and h["exit_watch"].get("active") and h.get("ts_code")]
    quotes = quotes_fn(need) if need else {}
    items: List[Dict[str, Any]] = []
    tri: List[str] = []
    for holding in holdings:
        exit_watch = holding.get("exit_watch") or {}
        quote = _quote_of(quotes, holding.get("ts_code"))
        price = float(quote["price"]) if _finite(quote.get("price")) else None
        state, levels = _holding_gate_item(price, exit_watch)
        if state is None:
            continue
        tri.append(state)
        stop = min([float(lv["price"]) for lv in levels
                    if lv["level_type"] == "STOP_LOSS" and lv["armed"]], default=None)
        items.append({
            "ts_code": holding.get("ts_code"), "name": holding.get("name"),
            "price": price, "price_source": quote.get("source") if price is not None else None,
            "stop_price": stop,
            "distance_pct": (round((price - stop) / stop * 100.0, 3)
                             if price is not None and stop else None),
            "state": state, "trigger_levels": levels,
        })
    g2_asof = view.get("source_updated_at")
    g2_stale = _data_stale(g2_asof)
    g2_basis = "updated_at"
    if view.get("trade_mode") == "paper":
        td = view.get("source_trade_date")
        if td:
            g2_stale = str(td).replace("-", "") != datetime.now().strftime("%Y%m%d")
            g2_basis = "trade_date"
    g2 = {"state": _worst(tri), "items": items,
          "data_asof": g2_asof, "data_stale": g2_stale, "data_basis": g2_basis,
          "trade_mode": view.get("trade_mode"), "holdings_source": view.get("source"),
          "holdings_stale": view.get("stale"), "labels_available": view.get("labels_available")}

    selection = _selection_source(ctx)
    sel_date = str(selection.get("date") or "")
    sel_codes = selection.get("codes") or []
    today8 = datetime.now().strftime("%Y%m%d")
    g3_asof = selection.get("asof")
    if not selection or not sel_date:
        g3 = {"state": "UNKNOWN", "reason": "selection 当日产物未产出（无信号库可读）",
              "pool_size": 0, "signals": [], "data_asof": g3_asof, "data_stale": True}
    elif sel_date != today8:
        g3 = {"state": "UNKNOWN", "reason": f"selection 当日产物未产出（库内最新 {sel_date}）",
              "pool_size": 0, "signals": [], "data_asof": g3_asof, "data_stale": True}
    else:
        g3 = {"state": "YELLOW" if sel_codes else "GREEN",
              "reason": (f"当日选股命中 {len(sel_codes)} 只" if sel_codes else "当日选股无命中"),
              "pool_size": len(sel_codes), "signals": sel_codes,
              "data_asof": g3_asof, "data_stale": _data_stale(g3_asof)}
    return {"version": GATES_VERSION, "updated_at": _now(),
            "gate1": dict(g1, name="情绪门"),
            "gate2": dict(g2, name="持仓门"),
            "gate3": dict(g3, name="选股门")}


# ---------------------------------------------------------------- 告警三源合一
def _pnl_items(holdings: Sequence[Dict[str, Any]], quotes_fn) -> Dict[str, Any]:
    """持仓实时盈亏 + 止损距离（旧端 `_pnl_items_from_holdings` 同字段）。

    与旧端一致：**先按需收集代码再批量取价**（`quotes_fn(need)`），不是全量取。
    """
    need = [h.get("ts_code") for h in holdings if h.get("ts_code")]
    quotes = quotes_fn(need) if need else {}
    out: List[Dict[str, Any]] = []
    states: List[str] = []
    for holding in holdings:
        quote = _quote_of(quotes, holding.get("ts_code"))
        price = float(quote["price"]) if _finite(quote.get("price")) else None
        cost, qty = holding.get("cost_price"), holding.get("qty")
        pnl_amount = pnl_pct = None
        if price is not None and _finite(cost) and _finite(qty):
            pnl_amount = round((price - float(cost)) * float(qty), 2)
            pnl_pct = round((price - float(cost)) / float(cost) * 100.0, 3) if float(cost) else None
        exit_watch = holding.get("exit_watch") or {}
        stops = [lv for lv in (exit_watch.get("trigger_levels") or [])
                 if lv.get("level_type") == "STOP_LOSS" and lv.get("armed") and _finite(lv.get("price"))]
        stop = min(float(lv["price"]) for lv in stops) if stops else None
        state = _stop_distance_state(price, stop)
        states.append(state)
        out.append({"ts_code": holding.get("ts_code"), "name": holding.get("name"),
                    "qty": qty, "cost_price": cost, "price": price,
                    "price_source": quote.get("source") if price is not None else None,
                    "pnl_amount": pnl_amount, "pnl_pct": pnl_pct,
                    "stop_price": stop,
                    "stop_distance_pct": (round((price - stop) / stop * 100.0, 3)
                                          if price is not None and stop else None),
                    "stop_state": state})
    return {"state": _worst(states), "items": out}


def _plan_trigger(price: Optional[float], key_levels: Any, enabled: Any = True) -> Optional[Dict[str, Any]]:
    """单预案触发三态（旧端 `evaluate_plan_trigger`）：判据 = key_levels.stop_loss。"""
    if enabled is False:
        return None
    trigger = (key_levels or {}).get("stop_loss")
    if not _finite(trigger):
        return None
    trigger = float(trigger)
    if not _finite(price) or float(price) <= 0:
        return {"state": "UNKNOWN", "trigger_price": trigger, "triggered": False,
                "inferred_status": None, "distance_pct": None}
    p = float(price)
    if p <= trigger:
        return {"state": "RED", "trigger_price": trigger, "triggered": True,
                "inferred_status": "EXECUTING", "distance_pct": 0.0}
    dist = (p - trigger) / trigger * 100.0
    if dist < ALERT_NEAR_PCT:
        return {"state": "YELLOW", "trigger_price": trigger, "triggered": False,
                "inferred_status": None, "distance_pct": round(dist, 3)}
    return {"state": "GREEN", "trigger_price": trigger, "triggered": False,
            "inferred_status": None, "distance_pct": round(dist, 3)}


def _near_triggers(battle_plan: Optional[Dict[str, Any]], quotes_fn) -> Dict[str, Any]:
    """作战表临近触发（旧端 `build_near_triggers` 同字段）。"""
    if not battle_plan:
        return {"state": "UNKNOWN", "reason": "battle_plan.json 缺失", "items": []}
    ops = battle_plan.get("operations") or []
    need = [o.get("ts_code") for o in ops
            if o.get("enabled") is not False
            and _finite((o.get("key_levels") or {}).get("stop_loss")) and o.get("ts_code")]
    quotes = quotes_fn(need) if need else {}
    items: List[Dict[str, Any]] = []
    states: List[str] = []
    for op in ops:
        key_levels = op.get("key_levels") or {}
        quote = _quote_of(quotes, op.get("ts_code"))
        price = float(quote["price"]) if _finite(quote.get("price")) else None
        result = _plan_trigger(price, key_levels,
                               op.get("enabled") if op.get("enabled") is not None else True)
        if result is None:
            continue
        states.append(result["state"])
        items.append({"ts_code": op.get("ts_code"), "name": op.get("name"),
                      "plan": op.get("plan"), "action_type": op.get("action_type"),
                      "trigger_price": result["trigger_price"], "price": price,
                      "price_source": quote.get("source") if price is not None else None,
                      "distance_pct": result["distance_pct"], "state": result["state"],
                      "triggered": result["triggered"],
                      "inferred_status": result["inferred_status"]})
    return {"state": _worst(states), "items": items}


def _source_health(intraday: Optional[Dict[str, Any]], quote_src: Optional[Dict[str, Any]],
                   now_dt: Optional[datetime] = None) -> Dict[str, Any]:
    """数据源连续降级判定（旧端 `evaluate_source_health` 同阈值同文案）。"""
    now = now_dt or datetime.now()
    subs: List[Dict[str, Any]] = []
    for label, doc, ts_key in (("intraday", intraday, "generated_at"),
                               ("watchlist_quotes", quote_src, "quote_asof")):
        if not doc:
            subs.append({"source": label, "state": "RED", "reason": f"{label}.json 缺失"})
            continue
        meta = doc.get("meta") or {}
        src = str(meta.get("source") or "")
        if "降级" in src:
            subs.append({"source": label, "state": "RED",
                         "reason": f"{label} meta.source 标注降级（{src}）"})
            continue
        ts = _parse_ts(meta.get(ts_key) if label == "intraday" else doc.get(ts_key))
        if ts is None:
            subs.append({"source": label, "state": "YELLOW",
                         "reason": f"{label} 无有效时间戳，新鲜度不可判"})
            continue
        age = (now - ts).total_seconds()
        if age < -ALERT_FUTURE_TOLERANCE_SECONDS:
            subs.append({"source": label, "state": "YELLOW",
                         "reason": f"{label} 时间戳在未来（时钟倒挂或数据异常）：超前 {int(-age)}s >{ALERT_FUTURE_TOLERANCE_SECONDS}s"})
        elif age > ALERT_STALE_SECONDS:
            subs.append({"source": label, "state": "RED",
                         "reason": f"{label} 连续降级：最后刷新 {int(age)}s 前 >{ALERT_STALE_SECONDS}s"})
        else:
            subs.append({"source": label, "state": "GREEN", "reason": f"{label} 新鲜（{int(age)}s 前）"})
    return {"state": _worst([s["state"] for s in subs]), "subs": subs}


def build_alerts(ctx: Any, quotes_fn=None, now_dt: Optional[datetime] = None) -> Dict[str, Any]:
    """全局告警三源合一（旧端 `build_alerts_payload` 同字段）：

    `{version,updated_at,alerts,worst,near_triggers,pnl,source_health,holdings}`。
    三源：①持仓触线 ②买点信号触发（intraday.signals status=TRIGGERED）③数据源连续降级。
    **只读**：内存态装配，绝不写生产 JSON、不改 battle_plan 状态。
    """
    quotes_fn = quotes_fn or (lambda codes: _quotes_for(ctx, codes))
    positions = _panel(ctx, "positions")
    battle_plan = _panel(ctx, "battle_plan")
    intraday = _panel(ctx, "intraday")
    view = _resolve_holdings(ctx, positions)

    holdings = view.get("items") or []
    if view.get("source_missing"):
        pnl = {"state": "UNKNOWN",
               "reason": f"{view.get('source')} 缺失（{view.get('trade_mode')} 模式持仓权威源未生成）",
               "items": []}
    else:
        pnl = _pnl_items(holdings, quotes_fn)

    near = _near_triggers(battle_plan, quotes_fn)
    wq = _panel(ctx, "watchlist_quotes")
    quote_src = ({"quote_asof": wq.get("asof"),
                  "meta": {"source": (wq.get("meta") or {}).get("source")}}
                 if isinstance(wq, dict) else None)
    health = _source_health(intraday, quote_src, now_dt)

    alerts: List[Dict[str, Any]] = []
    for item in pnl.get("items") or []:
        if item["stop_state"] == "RED":
            alerts.append({"level": "RED", "source": "holding_line", "tab": "battle_plan",
                           "ts_code": item["ts_code"], "name": item["name"],
                           "message": f"现价 {_num(item['price'])} 已触/破武装止损线 {_num(item['stop_price'])}"})
        elif item["stop_state"] == "YELLOW":
            alerts.append({"level": "YELLOW", "source": "holding_line", "tab": "battle_plan",
                           "ts_code": item["ts_code"], "name": item["name"],
                           "message": f"现价 {_num(item['price'])} 距止损线 {_num(item['stop_price'])} <{ALERT_PNL_STOP_YELLOW_PCT:.0f}%"})
    for sig in ((intraday or {}).get("signals") or []):
        if sig.get("status") == "TRIGGERED":
            alerts.append({"level": "RED", "source": "signal_fired", "tab": "intraday",
                           "ts_code": sig.get("ts_code"), "name": sig.get("name"),
                           "message": f"买点信号触发 {sig.get('signal_id') or ''} "
                                      f"{_SIG_SOURCE_CN.get(sig.get('source'), sig.get('source') or '')}".strip()})
    if health["state"] != "GREEN":
        for sub in health.get("subs") or []:
            if sub["state"] != "GREEN":
                alerts.append({"level": sub["state"], "source": "source_degrade",
                               "tab": "dashboard", "ts_code": None, "name": None,
                               "message": sub["reason"]})

    return {"version": ALERTS_VERSION,
            "updated_at": (now_dt or datetime.now()).strftime("%Y-%m-%d %H:%M:%S"),
            "alerts": alerts, "worst": _worst([a["level"] for a in alerts]),
            "near_triggers": near, "pnl": pnl, "source_health": health,
            "holdings": {"trade_mode": view.get("trade_mode"),
                         "source": view.get("source"),
                         "source_missing": view.get("source_missing"),
                         "labels_available": view.get("labels_available"),
                         "label_stale": view.get("stale")}}


# ---------------------------------------------------------------- 实盘持仓
def build_real_positions(ctx: Any, quotes_fn=None) -> Dict[str, Any]:
    """实盘持仓快照（旧端 `api_real_positions` 同字段）：原样 + `positions_list`。

    - `positions_list` 走统一持仓明细（现价/盈亏%/当日收益/市值/仓位占比/建仓时间）；
    - `market_value_total` 仅在 `positions` 非空时出现（旧端同条件，**空仓不多给键**）；
    - 文件未生成 → 404（前端按「实盘数据待连接」渲染，不误报空仓）。
    """
    data = _panel_or_404(ctx, "real_positions")
    quotes_fn = quotes_fn or (lambda codes: _quotes_for(ctx, codes))
    out = dict(data)
    raw = [p for p in (data.get("positions") or []) if isinstance(p, dict)]
    if raw:
        name_index = _name_index(ctx)
        quotes = quotes_fn([p.get("code") for p in raw])
        pairs = [(p.get("code"), {"name": p.get("name"), "qty": p.get("qty"),
                                  "available_qty": p.get("available_qty"),
                                  "cost_price": p.get("cost_price"),
                                  "opened_at": p.get("opened_at") or p.get("open_date")})
                 for p in raw]
        rows, agg = _holdings_view_rows(pairs, quotes, name_index)
        out["positions_list"] = rows
        out["market_value_total"] = agg["market_value"]
    else:
        out["positions_list"] = []
    return out


# ---------------------------------------------------------------- 全局搜索
def _suffix_of(code6: str) -> Optional[str]:
    """6 位码 → 交易所后缀；无法判定 → None（不猜、不下发）。

    ⚠ 与旧端的**唯一实质差异**：旧端 `common.names.name_map()` 的键是**完整 ts_code**
    （`688012.SH`，后缀来自 TDX 官方名表），这里 `ctx.json_read("stock_names")` 只有 6 位码，
    后缀必须**从号段推断**。判定顺序（先窄后宽，避免 9 开头被一律判成 SH）：
      920xxx               → BJ（北交所新号段）
      880/881/884/885      → SH（同花顺板块指数）
      4xxxxx / 8xxxxx      → BJ
      5xxxxx / 6xxxxx / 9x → SH
      0xxxxx / 1xxxxx / 3x → SZ
    推断不出 → None（结果里不出现，宁可少召回也不下发错后缀）。
    """
    if len(code6) != 6 or not code6.isdigit():
        return None
    if code6.startswith("920"):
        return "BJ"
    if code6[:3] in ("880", "881", "884", "885"):
        return "SH"
    head = code6[0]
    if head in "48":
        return "BJ"
    if head in "569":
        return "SH"
    if head in "013":
        return "SZ"
    return None


def _is_etf(code6: str, suffix: str) -> bool:
    return (suffix == "SH" and code6[:1] == "5") or (suffix == "SZ" and code6[:1] == "1")


def _is_board(code6: str, suffix: str) -> bool:
    return suffix == "SH" and code6[:3] in ("880", "881", "884", "885")


def _search_sectors(ctx: Any, keyword: str, keyword_lower: str, limit: int) -> List[Dict[str, Any]]:
    """板块搜索：`amv_board.db::sector_meta(level,code,name)` 按 代码/名称 命中。

    ⚠ 旧端还支持**拼音首字母/全拼**（pypinyin）。本批不引入新依赖，只做 代码+名称 匹配；
    返回**结构完全相同**（少的是命中的召回面，不是字段），已记录。
    """
    out: List[Dict[str, Any]] = []
    try:
        conn = ctx.db_read("amv_board.db")
    except Exception:  # noqa: BLE001 - 库不可用 → 空结果（不编造）
        return out
    py = _pinyin_map(ctx)
    kw_norm = _norm_text(keyword)
    try:
        for level, code, name in conn.execute("SELECT level, code, name FROM sector_meta"):
            if not name:
                continue
            p = py.get(str(name)) or {}
            if _code_hit(kw_norm, str(code), str(code).lower()) \
                    or _text_hit(kw_norm, _norm_text(name),
                                 str(p.get("f") or ""), str(p.get("i") or "")):
                out.append({"code": code, "name": name, "level": level, "kind": "板块"})
    except Exception:  # noqa: BLE001
        out = []
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    out.sort(key=lambda x: (0 if (str(x["name"]).lower().startswith(keyword_lower)
                                 or str(x["code"]).lower().startswith(keyword_lower)) else 1,
                            len(str(x["name"]))))
    return out[:limit]


def build_search(ctx: Any, q: Any = "", limit: Any = None) -> Dict[str, Any]:
    """全局搜索（旧端 `api_search_all` 同结构）：`{q, stocks:[{code,name,kind}], sectors:[...]}`。

    与任务书略有出入：旧端是 **POST + JSON body**（中文走 query 会乱码），任务书列的是
    `GET ?q=`。这里两个都接（GET 用 query、POST 用 body），响应结构与旧端逐字一致。
    股票源 = `ctx.json_read("stock_names")`（东财全量表 6 位码→名称）。
    """
    keyword = str(q or "").strip().replace("　", " ")
    if not keyword:
        return {"q": "", "stocks": [], "sectors": []}
    keyword_lower = keyword.lower()
    tokens = [_norm_text(t) for t in keyword.split() if t.strip()]
    try:
        limit_n = max(1, min(int(limit or SEARCH_LIMIT_DEFAULT), SEARCH_LIMIT_MAX))
    except (TypeError, ValueError):
        limit_n = SEARCH_LIMIT_DEFAULT

    stocks: List[Dict[str, Any]] = []
    names = _name_index(ctx)
    py = _pinyin_map(ctx)
    for code6, nm in names.items():
        suffix = _suffix_of(str(code6))
        if suffix is None:
            continue
        label = str(nm or "")
        full = f"{code6}.{suffix}".lower()
        low = _norm_text(label)
        p = py.get(label) or {}
        pf, pi = str(p.get("f") or ""), str(p.get("i") or "")
        # 2026-10-28 主人要模糊搜索：代码后 3 位起 + 名称子串/拼音（≥ 2 字）；多关键词全命中
        if not all(_code_hit(t, str(code6), full) or _text_hit(t, low, pf, pi)
                   for t in tokens):
            continue
        if _is_board(str(code6), suffix):
            kind = "板块"
        elif _is_etf(str(code6), suffix):
            kind = "ETF"
        else:
            kind = "股票"
        stocks.append({"code": f"{code6}.{suffix}", "name": label, "kind": kind})

    def _rank(x: Dict[str, Any]):
        c6 = x["code"][:6]
        nm2 = x["name"]
        nm2_low = _norm_text(nm2)
        head = tokens[0] if tokens else keyword_lower
        p2 = py.get(nm2) or {}
        exact = 0 if (c6 == keyword_lower or nm2_low == _norm_text(keyword_lower)) else 1
        # 数字关键词命中的就是代码后缀：排在名称/拼音命中之前（主人按后 3 位找票的用法）
        code_pref = 0 if (head.isdigit() and len(head) >= 3 and c6.endswith(head)) else 1
        pref = 0 if (c6.endswith(head) or nm2_low.startswith(head)
                     or str(p2.get("i") or "").startswith(head)
                     or str(p2.get("f") or "").startswith(head)) else 1
        return (exact, code_pref, pref, len(nm2), x["code"])

    stocks.sort(key=_rank)
    stocks = stocks[:limit_n]

    return {"q": keyword, "stocks": stocks,
            "sectors": _search_sectors(ctx, keyword, keyword_lower, limit_n)}


# ---------------------------------------------------------------- 板块与个股研究（amv_research）
#
# 旧端：`engine/server.py::api_amv_research`（L2776）+ `_rs_base`（L2660）+ `_rs_pool`（L2521）
#       + `_rs_spark`（L2567）+ `_rs_v`（L2625）+ `_rs_parts`（L2652）。逐行对齐，只换取数通道。
# 口径铁律（主人 2026-10-18）：**不取源站任何字段**，全走自家库 ——
#   · 板块池/成分 = `ths_board.db`（同花顺二级 881xxx.TI / 三级 884xxx.TI / 自建 999xxx.TI）
#   · 逐股 AMV   = `amv_board.db::stock_amv`（只读日序，**禁重算**）
#   · 逐股 alive/sh = `stock_state`（分项拆解用；库内仅近 31 日）
#   · 大盘分母   = `daily_total`
# 指数贡献‰ = Δ占比 = (ΣAMV(d1)/T1 − ΣAMV(d0)/T0)×1000，与面板「日内演化」_evoContrib 同一式。
#
# 与旧端的四处差异（都是能力边界，不编造）：
#   1. `live=1` 实时口径（2026-10-24 已接）：旧端 `_rs_live()` 直接调
#      `executor.amv_live.stocks_snapshot()`；本插件经 core 门面 `ctx.amv_live` 拿同一份快照
#      （`app/core/amvlive.py`，含 25s/300s 缓存、线程超时、失败缓存），算路零复制。
#      取不到快照 → `live_mode=false` + `live_err` 给原因，分子分母全回收盘口径（与旧端同）。
#   2. 个股名称：旧端 `common.names.name_map()`；本插件用 `ctx.json_read("stock_names")`（6 位码→名称）。
#      两者都是本地名表，后者只有 6 位码 → 查不到回代码（与旧端 `nmap.get(s) or ... or s` 同序）。
#   3. 缩略线 MA10：旧端调 `amv_regime.sma`；本插件同样走 `ctx.lib("amv_regime")`，无第二份实现。
#   4. 北交判定：旧端走 `bj_scope`（不在 core 口径库白名单）→ 本插件按号段字面置实现，
#      实测两套判定在 board_member∪concept_member∪stock_amv 全量 6165 个代码上零差异（603 票重合）。
#      把「bj_scope 登记口径库」提请列入 HANDOFF（属 core 范围，本任务不改）。

RS_TTL = 120.0                 # 收盘口径缓存 TTL（旧端 `_RS_TTL`）
# 实时口径缓存 TTL（旧端 `api_amv_research` 里的 `20.0` 字面值）。
# 快照本身已带 25s 缓存（`app/core/amvlive.py`），这里再压一层是为了不重复跑装配
# （全A 榜每条带 SVG，一次装配不轻）。
RS_TTL_LIVE = 20.0
RS_MEMBER_CAP = 100            # 成分股表最多下发 100 行（2026-10-26 主人定：「看太多也没有意义，
                               #   按排序方式返回前 100 只就够」）。`n_members` 始终为真值总数
RS_ALL_CAP = 300               # 全A 榜最多 300 行（按指数贡献取头部）
RS_DAYS_MIN, RS_DAYS_MAX = 12, 60
RS_DAYS_DEFAULT = 30
RS_PREHEAT = 11                # 首端多取几日供 MA10 起算（days + 10 ⇒ 多 10 日，取 11 保险）
RS_SPARK_W, RS_SPARK_H, RS_SPARK_PAD = 100.0, 26.0, 2.0
RS_LEVELS = ("L2", "L3", "CONCEPT")
RS_LEVEL_LABEL = {"L2": "同花顺二级", "L3": "同花顺三级", "CONCEPT": "自建"}
# 北交号段（口径源 `夏天-activity/工具/bj_scope.py` → `track_local_rebuild.BJ_CODE_PREFIXES`）。
RS_BJ_PREFIXES = ("92", "43", "83", "87", "88")

_RS_CACHE: Dict[tuple, Tuple[float, Any]] = {}
_RS_CACHE_LOCK = threading.Lock()
_RS_CACHE_MAX = 24


def _rs_is_bj(code: Any) -> bool:
    """是否北交所（号段判定，与 `bj_scope.is_bj` 同口径；见模块头差异 4）。"""
    return str(code)[:2] in RS_BJ_PREFIXES


_CUSTOM_CODE_FN: List[Any] = []   # 2026-10-25：自建板块码判据缓存（唯一源 ths_board_pool.is_custom_code）


def _is_custom_board(code: Any) -> bool:
    """是否自建板块码（999 段）。唯一源 = `夏天-activity/工具/ths_board_pool.is_custom_code`。

    2026-10-25：概念层改只算自建板块后，凡「能当板块点进去看走势/排名/成分」的入口
    都必须过本判据；纯名字反查/个股概念标签不过滤（主人定的分界线）。
    读不到工具目录（插件不应直接依赖业务工具）→ 保守返回 True（不过滤、不丢板块）。
    """
    if not _CUSTOM_CODE_FN:
        import importlib.util  # noqa: PLC0415
        p = str(core_paths.SUMMER_TOOLS_DIR / "ths_board_pool.py")
        fn = None
        if os.path.exists(p):
            spec = importlib.util.spec_from_file_location("_pv_ths_board_pool", p)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            fn = m.is_custom_code
        _CUSTOM_CODE_FN.append(fn)
    fn = _CUSTOM_CODE_FN[0]
    return True if fn is None else fn(code)


def _rs_cache_get(key: tuple, ttl: float) -> Optional[Any]:
    with _RS_CACHE_LOCK:
        hit = _RS_CACHE.get(key)
    if hit and (time.time() - hit[0]) < ttl:
        return hit[1]
    return None


def _rs_cache_put(key: tuple, value: Any) -> None:
    with _RS_CACHE_LOCK:
        _RS_CACHE[key] = (time.time(), value)
        if len(_RS_CACHE) > _RS_CACHE_MAX:
            for k in list(_RS_CACHE)[:-_RS_CACHE_MAX]:
                _RS_CACHE.pop(k, None)


# ---------------------------------------------------------------- 盘后落盘缓存（2026-11-07）
#
# 主人：「收盘后的数据就已经固定下来了，没必要每次都重拉重算」。
# 本端冷启动首次 6.8s（_rs_base 读 25 万行建矩阵），而且只在进程内存里；
# 重启后第一个打开盘中页的人又要等一次。把**收盘口径**结果写进
# `data/panel_v2/_cache/compat/amv_research.json`，按 amv_board.db / ths_board.db
# 的 mtime+size 失效：库不变就重启后也能秒开，库更新了（每天盘后重建）就重算一次。
#
# 边界：
#   · 只缓存 want_live=False 的结果（实时口径随快照变，不落盘）；
#   · 盘后时段（周末 / 16:10 后 / 09:15 前）把 live 参数当 0——收盘后快照已停，
#     结果与收盘口径一致；盘中仍按 live 请求实时算。
#     **16:10 的依据**（主人 2026-11-07 校正）：15:00 只是收市，数据还没重建完，
#     15:00–16:10 之间引用的仍是盘中/前一日库；收盘取数主链 `OH_PostCloseChain`
#     周一～五 16:10 才跑（登记册 A1 T36）。
#   · 读坏/写坏只退化为现算，不影响正确性。
_RS_DISK_NAME = "amv_research"
_RS_DISK_DBS = ("amv_board.db", "ths_board.db")


def _rs_is_postclose(ctx: Any) -> bool:
    """收盘后（数据固定）时段。唯一口径 = core `trade_clock.postclose()`
    （非交易日，或交易日 >= 16:40；16:40 = 链 READY_DEADLINE，16:21 才写完主库）。

    日历不可用/门面缺失 → 返回 False（宁可现算，不拿旧值当固定值）。
    """
    try:
        return bool(ctx.trade_clock.postclose())
    except Exception:  # noqa: BLE001 - 不可用就按“未就绪”处理
        return False


def _rs_disk_version(ctx: Any) -> str:
    """两个源库的 mtime+size；任一不可读 → 空串（不落盘）。"""
    parts: List[str] = []
    for name in _RS_DISK_DBS:
        try:
            conn = ctx.db_read(name)
        except Exception:  # noqa: BLE001 - 缺库/未声明 → 不缓存
            return ""
        try:
            rows = conn.execute("PRAGMA database_list").fetchall()
        except Exception:  # noqa: BLE001
            return ""
        finally:
            with contextlib.suppress(Exception):
                conn.close()
        p = str(rows[0][2]) if rows and len(rows[0]) >= 3 else ""
        if not p:
            return ""
        try:
            st = os.stat(p)
        except OSError:
            return ""
        parts.append("%s:%d:%d" % (name, st.st_mtime_ns, st.st_size))
    return "|".join(parts)


def _rs_disk_key(key: tuple) -> str:
    return "|".join(str(x) for x in key)


def _rs_db_latest(ctx: Any) -> str:
    """amv_board.db::stock_amv 最新交易日（数据日，不是生成时间）；不可读 → 空串。"""
    try:
        conn = ctx.db_read("amv_board.db")
    except Exception:  # noqa: BLE001
        return ""
    try:
        row = conn.execute("SELECT MAX(date) FROM stock_amv").fetchone()
        return str(row[0]) if row and row[0] else ""
    except Exception:  # noqa: BLE001
        return ""
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def _rs_disk_get(ctx: Any, key: tuple, version: str) -> Optional[Dict[str, Any]]:
    if not version:
        return None
    try:
        doc = ctx.cache_read(_RS_DISK_NAME)
    except Exception:  # noqa: BLE001 - 首次/未声明/损坏 → 现算
        return None
    if not isinstance(doc, dict) or doc.get("version") != version:
        return None
    items = doc.get("items")
    if not isinstance(items, dict):
        return None
    hit = items.get(_rs_disk_key(key))
    if not isinstance(hit, dict):
        return None
    # 铁律（主人 2026-11-07）：数据日必须等于当前库的最新日，
    # 不能把昨天的缓存当今天的结果返回（哪怕 mtime 因回填/拷贝没变）。
    if str(hit.get("asof") or "") != _rs_db_latest(ctx):
        return None
    return hit


def _rs_disk_put(ctx: Any, key: tuple, version: str, payload: Dict[str, Any]) -> None:
    if not version:
        return
    try:
        doc = ctx.cache_read(_RS_DISK_NAME)
    except Exception:  # noqa: BLE001
        doc = None
    if not isinstance(doc, dict) or doc.get("version") != version or not isinstance(doc.get("items"), dict):
        doc = {"version": version, "items": {}}
    doc["items"][_rs_disk_key(key)] = payload
    try:
        ctx.cache_write(_RS_DISK_NAME, doc)
    except Exception:  # noqa: BLE001 - 写失败只是下次多算一次
        pass


def _rs_pool(ctx: Any, level: str) -> Tuple[Dict[str, str], Dict[str, List[str]], str]:
    """板块池 → (names{code:name}, members{code:[stock6]}, 中文标签)。唯一源 = `ths_board.db`。

    口径澄清（旧端 2026-10-18 主人当场纠错）：这是**同花顺行业板块，不是申万**。
    二级码 881xxx.TI、三级码 884xxx.TI；表里的 `sw_level` 只是「层级」的历史字段名。

    2026-10-25 概念层口径切换（主人定）：CONCEPT 层只留**自建段**（999，当前仅 999001 物理AI）。
    本函数是「板块入口」（研究页概念层能点进去看走势/排名/成分），故必须过滤；
    纯名字反查/个股概念标签仍读全表。判据唯一源 = ths_board_pool.is_custom_code。
    """
    names: Dict[str, str] = {}
    members: Dict[str, List[str]] = {}
    conn = ctx.db_read("ths_board.db")
    try:
        if level == "CONCEPT":
            names = {str(c): n for c, n in conn.execute("SELECT code,name FROM concept")
                     if _is_custom_board(str(c))}
            for c, s in conn.execute("SELECT concept_code, stock FROM concept_member"):
                if str(c) in names:
                    members.setdefault(str(c), []).append(str(s))
            return names, members, RS_LEVEL_LABEL["CONCEPT"]
        sw = 3 if level == "L3" else 2
        names = {str(c): n for c, n in conn.execute(
            "SELECT code,name FROM board WHERE sw_level=?", (sw,))}
        for c, s in conn.execute(
                "SELECT m.board_code, m.stock FROM board_member m "
                "JOIN board b ON b.code=m.board_code WHERE b.sw_level=?", (sw,)):
            if str(c) in names:
                members.setdefault(str(c), []).append(str(s))
    finally:
        with contextlib.suppress(Exception):
            conn.close()
    return names, members, RS_LEVEL_LABEL.get(level, RS_LEVEL_LABEL["L2"])


def _rs_ffill(vals: Sequence[Optional[float]]) -> List[float]:
    """前值填充（None → 上一有效值）：停牌按 0 入均线会把 MA 拖低，产生假多空。"""
    out: List[float] = []
    last = 0.0
    for v in vals:
        if v is None:
            out.append(last)
        else:
            last = float(v)
            out.append(last)
    return out


def _rs_spark(series: Sequence[Optional[float]],
              ma: Sequence[Optional[float]]) -> Optional[Dict[str, Any]]:
    """近 N 日 AMV + MA10 → 多空分段 path（viewBox 100×26）。旧端 `_rs_spark` 逐行照搬。

    多空规则：该点 AMV ≥ MA10 → 多头（红）；否则空头（绿）。切点回接上一点保证视觉连续；
    缺点（停牌 NULL）断线。返回 {up,dn,ma,bias,up_last}；点数不足 → None。
    """
    n = len(series)
    pts = [v for v in series if v is not None] + [v for v in ma if v is not None]
    if len(pts) < 4:
        return None
    lo, hi = min(pts), max(pts)
    if hi <= lo:
        hi = lo + 1.0
    w, h, pad = RS_SPARK_W, RS_SPARK_H, RS_SPARK_PAD
    span = h - 2 * pad

    def X(i: int) -> int:
        return int(round(i / (n - 1) * w)) if n > 1 else 0

    def Y(v: float) -> int:
        return int(round(h - pad - (v - lo) / (hi - lo) * span))

    runs: List[Tuple[bool, List[Tuple[int, int]]]] = []
    brk = True
    for i, v in enumerate(series):
        if v is None:
            brk = True
            continue
        m = ma[i] if i < len(ma) else None
        if m is None:
            brk = True
            continue
        up = v >= m
        if not runs or runs[-1][0] != up:
            runs.append((up, [runs[-1][1][-1]] if (runs and not brk) else []))
        elif brk:
            runs.append((up, []))
        runs[-1][1].append((X(i), Y(v)))
        brk = False
    out = {"up": "", "dn": "", "ma": ""}
    for up, p in runs:
        if len(p) < 2:
            continue
        out["up" if up else "dn"] += "M" + "L".join("%d,%d" % xy for xy in p) + " "
    mpts = [(X(i), Y(v)) for i, v in enumerate(ma) if v is not None]
    if len(mpts) >= 2:
        out["ma"] = "M" + "L".join("%d,%d" % xy for xy in mpts)
    last = None
    for i in range(n - 1, -1, -1):
        if series[i] is not None and i < len(ma) and ma[i] is not None:
            last = (series[i], ma[i])
            break
    out["bias"] = round((last[0] / last[1] - 1.0) * 100, 2) if (last and last[1]) else None
    out["up_last"] = (last[0] >= last[1]) if last else None
    return out


def _rs_base(ctx: Any, level: str, days: int) -> Dict[str, Any]:
    """重活一次（按 level+days 缓存 RS_TTL 秒）：日期窗 / 逐股 AMV 矩阵 / 两日 alive-sh / 板块池 / 板块日序。

    设计初心与旧端 `_rs_base` 一致：把「读 25 万行 + 聚合 90 板块」与「按选中板块出表」拆开，
    重活只随交易日/级别/窗口变，不随选中板块变（否则换一个板块就要重跑 1.1s）。
    """
    ck = ("_base", level, days)
    hit = _rs_cache_get(ck, RS_TTL)
    if hit is not None:
        return hit
    names_s, members_s, label = _rs_pool(ctx, level)
    conn = ctx.db_read("amv_board.db")
    try:
        row = conn.execute("SELECT MAX(date) FROM stock_amv").fetchone()
        d1 = row[0] if row else None
        if not d1:
            return {"ok": False, "detail": "stock_amv 无数据"}
        tot = {d: v for d, v in conn.execute(
            "SELECT date,total FROM daily_total WHERE date<=?", (d1,))}
        dates = [r[0] for r in conn.execute(
            "SELECT DISTINCT date FROM stock_amv WHERE date<=? ORDER BY date DESC LIMIT ?",
            (d1, days + RS_PREHEAT))]
        dates = sorted(d for d in dates if tot.get(d))
        if len(dates) < 3:
            return {"ok": False, "detail": "交易日不足"}
        d0, d1 = dates[-2], dates[-1]
        T0, T1 = float(tot[d0]), float(tot[d1])
        pos = {d: i for i, d in enumerate(dates)}
        nD = len(dates)
        ser: Dict[str, List[Optional[float]]] = {}
        for d, s, a in conn.execute(
                "SELECT date,stock,amv FROM stock_amv WHERE date>=? AND date<=?", (dates[0], d1)):
            r = ser.get(str(s))
            if r is None:
                r = ser[str(s)] = [None] * nD
            r[pos[d]] = (float(a) if a is not None else 0.0)
        st: Dict[str, Dict[str, Tuple[Optional[float], Optional[float]]]] = {}
        for d in (d0, d1):
            box: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
            for s, al, sh in conn.execute(
                    "SELECT stock,alive,sh FROM stock_state WHERE date=?", (d,)):
                box[str(s)] = (float(al) if al is not None else None,
                               float(sh) if sh is not None else None)
            st[d] = box
    finally:
        with contextlib.suppress(Exception):
            conn.close()

    # 板块池剔北交（与 amv_live 聚合 mask 同源）⇒ Σ成分股 == 板块值，成分股表与板块同口径
    pools: Dict[str, List[str]] = {}
    for c, ms in members_s.items():
        lst = [s for s in ms if not _rs_is_bj(s)]
        if lst:
            pools[c] = lst
    bser: Dict[str, List[float]] = {}
    for c, lst in pools.items():
        acc = [0.0] * nD
        for s in lst:
            r = ser.get(s)
            if r is None:
                continue
            for i in range(nD):
                if r[i]:
                    acc[i] += r[i]
        bser[c] = acc
    out = {"ok": True, "names": names_s, "label": label, "dates": dates,
           "d0": d0, "d1": d1, "T0": T0, "T1": T1, "nD": nD,
           "ser": ser, "st": st, "pools": pools, "bser": bser}
    _rs_cache_put(ck, out)
    return out


def _rs_v(ser: Dict[str, List[Optional[float]]], s: str, i: int) -> float:
    """取某票第 i 个交易日的 AMV（缺行/停牌 NULL → 0.0，与 v9 聚合 contrib=0 同语义）。"""
    r = ser.get(s)
    if r is None:
        return 0.0
    v = r[i]
    return 0.0 if v is None else float(v)


def _rs_note(level: str) -> str:
    note = ("指数贡献‰ = Δ占比（与「日内演化」同式）；分项 + 大盘基准 = 指数贡献；"
            "缩略线 = 活跃市值对 MA10（与工作台详情卡同规则），红多/绿空")
    if level == "CONCEPT":
        note += "；自建板块多对多归属，各板块贡献**不可相加**（合计不为 0）"
    return note


def _rs_stale_for(level: str) -> Optional[Dict[str, Any]]:
    """同层级最近一次成功的板块级结果（EOD 重建窗口期兜底，旧端 2026-10-22 晚同款）。"""
    best: Optional[Tuple[float, Dict[str, Any]]] = None
    with _RS_CACHE_LOCK:
        items = list(_RS_CACHE.items())
    for k, v in items:
        if not (isinstance(k, tuple) and len(k) >= 5 and k[0] == level):
            continue
        if isinstance(v, tuple) and len(v) > 1 and isinstance(v[1], dict) and v[1].get("ok"):
            if best is None or v[0] > best[0]:
                best = (v[0], v[1])
    return best[1] if best else None


def build_amv_research(ctx: Any, level: Any = "L2", code: Any = "", days: Any = 30,
                       live: Any = 0, scope: Any = "board") -> Dict[str, Any]:
    """板块与个股研究（旧 8800 `api_amv_research` 同字段）：板块排名 + 成分股排名 + 贡献分项 + 缩略线。

    参数：`level`=L2/L3/CONCEPT（非法 → L2）；`code`=选中板块（缺省取贡献第一名）；
          `scope`=board（中栏=成分股）/all（中栏=全A个股）；`days`=缩略线交易日数（12–60，默认 30）；
          `live`=1 请求实时口径（旧端 `_rs_live` 同口径，快照经 core 门面 `ctx.amv_live` 拿）；
          取不到快照 → 分子分母全回收盘口径，`live_mode=false` + `live_err` 标原因。
    返回键与旧端逐字一致：
      `{ok, asof, prev, live_mode, live_date, live_err, level, level_label, n, total_pct, note,
        boards[], sel, sel_name, sel_contrib, n_members, sel_parts, members[], scope}`
    """
    lvl = str(level or "L2").upper()
    if lvl not in RS_LEVELS:
        lvl = "L2"
    try:
        days_n = max(RS_DAYS_MIN, min(int(days or RS_DAYS_DEFAULT), RS_DAYS_MAX))
    except (TypeError, ValueError):
        days_n = RS_DAYS_DEFAULT
    try:
        want_live = bool(int(live or 0))
    except (TypeError, ValueError):
        want_live = False
    sel_code = str(code or "").strip()
    scope = "all" if str(scope or "").lower() == "all" else "board"

    # 2026-11-07 盘后落盘：收盘后数据固定，先查磁盘（库 mtime 未变即命中）。
    # 必须在 `_rs_base` 之前——那是冷启动 6.8s 的大头。
    if _rs_is_postclose(ctx):
        want_live = False           # 收盘后快照已停：统一走收盘口径
    disk_ver = _rs_disk_version(ctx)
    disk_key = (lvl, sel_code, days_n, 1 if want_live else 0, scope)
    if not want_live:
        disk_hit = _rs_disk_get(ctx, disk_key, disk_ver)
        if disk_hit is not None:
            return disk_hit

    try:
        parts_fn = ctx.lib("amv_contrib").parts
        sma_fn = ctx.lib("amv_regime").sma
    except Exception as exc:  # noqa: BLE001 - 口径库不可用 = 本端点无法算，不编造
        raise HTTPException(status_code=503,
                            detail="口径库不可用：%s: %s" % (type(exc).__name__, exc))

    base = _rs_base(ctx, lvl, days_n)
    if not base.get("ok"):
        # 收盘后 EOD 重建 amv_board.db 的窗口期：回退到同层级**最近一次成功结果**并打 stale 标记
        # （面板体现为「保留最后一刻数据 + 未更新」，而不是整卡空白）。
        stale = _rs_stale_for(lvl)
        if stale is not None:
            p = dict(stale)
            p["stale"] = True
            return p
        return {"ok": False, "detail": base.get("detail"), "boards": [], "members": []}

    # 缓存分两档（旧端 `_RS_CACHE` 同口径）：实时口径 20s，收盘口径 RS_TTL。
    # key 必须含 want_live 位；否则 live=1 会在 20s 内把收盘口径回写到 live 请求上。
    key = (lvl, sel_code, days_n, 1 if want_live else 0, scope)
    hit = _rs_cache_get(key, RS_TTL_LIVE if want_live else RS_TTL)
    if hit is not None:
        return hit

    names_s, label = base["names"], base["label"]
    d0, d1, T0, T1, nD = base["d0"], base["d1"], base["T0"], base["T1"], base["nD"]
    ser, st, pools, bser = base["ser"], base["st"], base["pools"], base["bser"]
    # ---- 实时口径（2026-10-24 接，旧端 `_rs_live` 同口径）----
    # 只换「分子」：快照日 > d1（EOD 还没含当日）→ 基期 d1、分母 T1；
    # 快照日 <= d1（EOD 已含当日）→ 基期 d0、分母 T0。当日双算的保护与旧端逐行一致。
    snap = None
    live_err = None
    if want_live:
        panel = None
        try:
            panel = getattr(ctx, "amv_live", None)
        except Exception as exc:  # noqa: BLE001 - 未声明权限 / core 旧版 → 如实降级
            live_err = "%s: %s" % (type(exc).__name__, exc)
        if panel is None:
            # 契约未提供（core 版本过低 / manifest 未声明 data.amv_live）→ 如实降级
            live_err = live_err or ("core 未提供 ctx.amv_live（manifest.data.amv_live 未声明），"
                                    "live=1 已回退收盘口径")
        else:
            try:
                snap = panel.snapshot()
            except Exception as exc:  # noqa: BLE001 - 门面不可用 = 回退收盘口径，不招摘
                snap = None
                live_err = "%s: %s" % (type(exc).__name__, exc)
            if snap is not None and str(snap.get("date") or "") < str(d1):
                snap = None                 # 快照归属日比 EOD 还旧 → 不采信（不拿旧 tick 充实时）
            if snap is None and live_err is None:
                try:
                    live_err = panel.last_error()   # 空快照 / 超时 / 异常原因（可能为 None）
                except Exception:  # noqa: BLE001 - 门面无 last_error 就当无原因
                    live_err = None
    _use_d1 = snap is not None and bool(snap.get("date")) and str(snap["date"]) > str(d1)
    i_base = (nD - 1) if _use_d1 else (nD - 2)
    d_base = d1 if _use_d1 else d0
    T_base = T1 if _use_d1 else T0
    lvs = snap.get("stocks") if snap else None
    T1v = float(snap.get("all_amv")) if (snap and snap.get("all_amv") is not None) else T1

    def _x1(s: str) -> float:
        """当日分子：有快照取快照逐股 AMV（缺行 → 0.0），否则取 EOD 末日值（旧端同文）。"""
        if lvs is None:
            return _rs_v(ser, s, nD - 1)
        row = lvs.get(s)
        return float(row[0]) if row else 0.0

    def _a1s1(s: str) -> Tuple[Any, Any]:
        """当日 alive/sh：有快照取快照行，否则取 st[d1]（旧端同文）。"""
        if lvs is None:
            return st[d1].get(s) or (None, None)
        row = lvs.get(s)
        return (row[1], row[2]) if row else (None, None)

    # ---- 板块主表：s1 用快照聚合（旧端 `sum(_x1(s) for s in lst)`），否则用 EOD 末值 ----
    rows: List[Dict[str, Any]] = []
    for c, lst in pools.items():
        bs = bser[c]
        s1 = (sum(_x1(s) for s in lst) if lvs is not None else bs[-1])
        s0 = bs[i_base]
        if s1 <= 0 or s0 <= 0:
            continue
        # 加权涨跌%（Σ涨跌分项 / Σ基期AMV）——与四分项同源，零额外行情依赖
        px_sum = base_sum = 0.0
        for s in lst:
            x0, x1 = _rs_v(ser, s, i_base), _x1(s)
            if x0 <= 0 or x1 <= 0:
                continue
            a0, sh0 = st[d_base].get(s) or (None, None)
            a1, sh1 = _a1s1(s)
            p = parts_fn(x0, x1, a0, sh0, a1, sh1)
            px_sum += p[0]
            base_sum += x0
        # 多取 10 日仅供 MA10 起算：否则前 9 个点无均线 → 整条缩略线只画出右半段
        full = [(v if v > 0 else None) for v in bs[-(days_n + 10):]]
        maf = sma_fn(_rs_ffill(full), 10)
        sp = _rs_spark(full[-days_n:], maf[-days_n:])
        rows.append({
            "c": c, "n": names_s.get(c, c),
            "contrib": round((s1 / T1v - s0 / T_base) * 1000.0, 3),
            "amv_pct": round((s1 / s0 - 1.0) * 100.0, 2),
            "px_pct": round(px_sum / base_sum * 100.0, 2) if base_sum > 0 else None,
            "bias": sp["bias"] if sp else None,
            "up": sp["up_last"] if sp else None,
            "sp": {"up": sp["up"], "dn": sp["dn"], "ma": sp["ma"]} if sp else None,
        })
    rows.sort(key=lambda x: -x["contrib"])
    for i, r in enumerate(rows):
        r["rk"] = i + 1
    selset = {r["c"] for r in rows}
    sel = sel_code if sel_code in selset else (rows[0]["c"] if rows else "")
    sel_row = next((r for r in rows if r["c"] == sel), None)

    members: List[Dict[str, Any]] = []
    n_members = 0
    sel_parts = None
    nmap = _name_index(ctx)

    def _ts_of(s: str) -> str:
        return s + "." + (_suffix_of(s) or "SZ")

    def _mk(s: str) -> Optional[Dict[str, Any]]:
        """单只个股一行（贡献/AMV/涨跌/缩略线）—— 成分股表与全A榜同一套算法。"""
        x0, x1 = _rs_v(ser, s, i_base), _x1(s)
        if x0 <= 0 and x1 <= 0:
            return None
        a0, sh0 = st[d_base].get(s) or (None, None)
        a1, sh1 = _a1s1(s)
        serr = ser.get(s) or []
        full = [(v if (v is not None and v > 0) else None) for v in serr[-(days_n + 10):]]
        maf = sma_fn(_rs_ffill(full), 10)
        sp = _rs_spark(full[-days_n:], maf[-days_n:])
        if x0 > 0 and x1 > 0:
            ra = (a1 / a0) if (a0 and a0 > 0 and a1 and a1 > 0) else 1.0
            pxp = (x1 / x0 / ra - 1.0) * 100.0    # p1/p0−1（amv/alive 反解，零额外行情依赖）
        else:
            pxp = None
        ts = _ts_of(s)
        return {
            "c": s, "ts": ts,
            "n": nmap.get(s) or nmap.get(ts) or s,
            "contrib": round((x1 / T1v - x0 / T_base) * 1000.0, 3),
            "amv_pct": round((x1 / x0 - 1.0) * 100.0, 2) if (x0 > 0 and x1 > 0) else None,
            "px_pct": round(pxp, 2) if pxp is not None else None,
            "bias": sp["bias"] if sp else None,
            "up": sp["up_last"] if sp else None,
            "sp": {"up": sp["up"], "dn": sp["dn"], "ma": sp["ma"]} if sp else None,
        }

    if scope == "all" or sel:
        if scope == "all":
            # 全A：先只算贡献排序（两次查表 + 一次除法），取前 N 才算缩略线——不拿 5000 条 SVG 打爆下传
            cand: List[Tuple[str, float]] = []
            for s in ser:
                x0, x1 = _rs_v(ser, s, i_base), _x1(s)
                if x0 > 0 and x1 > 0:
                    cand.append((s, x1 / T1v - x0 / T_base))
            cand.sort(key=lambda t: -t[1])
            n_members = len(cand)
            for s, _ in cand[:RS_ALL_CAP]:
                m = _mk(s)
                if m:
                    members.append(m)
        else:
            for s in pools.get(sel, []):
                x0, x1 = _rs_v(ser, s, i_base), _x1(s)
                if x0 <= 0 and x1 <= 0:
                    continue
                m = _mk(s)
                if m:
                    members.append(m)
        members.sort(key=lambda x: -x["contrib"])
        for i, r in enumerate(members):
            r["rk"] = i + 1
        if scope != "all":
            n_members = len(members)
            if n_members > RS_MEMBER_CAP:          # 2026-10-26 主人定：按当前排序只下发前 100 只
                members = members[:RS_MEMBER_CAP]

    # 贡献分项：只要选中了板块就算（与中栏看全A还是看成分股无关——右栏详情始终对着选中板块）
    if sel and sel in pools:
        try:
            pp = [0.0] * 5
            for s in pools[sel]:
                x0, x1 = _rs_v(ser, s, i_base), _x1(s)
                if x0 <= 0 and x1 <= 0:
                    continue
                a0, sh0 = st[d_base].get(s) or (None, None)
                a1, sh1 = _a1s1(s)
                q = parts_fn(x0, x1, a0, sh0, a1, sh1)
                for k in range(5):
                    pp[k] += q[k]
            bsel = bser.get(sel) or []
            amv0 = bsel[i_base] if (0 <= i_base < len(bsel)) else 0.0
            sel_parts = {
                "px": round(pp[0] / T1v * 1000.0, 3),
                "sh": round(pp[1] / T1v * 1000.0, 3),
                "act": round(pp[2] / T1v * 1000.0, 3),
                "new": round(pp[3] / T1v * 1000.0, 3),
                "susp": round(pp[4] / T1v * 1000.0, 3),
                "den": round(-amv0 * (T1v / T_base - 1.0) / T1v * 1000.0, 3),
                "total": (sel_row or {}).get("contrib"),
                "cover": round((pp[0] + pp[1] + pp[2] + pp[3] + pp[4]
                                - amv0 * (T1v / T_base - 1.0)) / T1v * 1000.0, 3),
            }
        except Exception:  # noqa: BLE001 - 分项失败不影响主表
            sel_parts = None

    # 全A 汇总分项（2026-11-06 主人：全A 视图下也要看 AMV 贡献分项分解）。
    # 只对 scope=all 算，口径与板块分项同一套 parts_fn / T1v / T_base：
    #   四档 = Σ 逐股 parts（单边缺数也计入，与板块分支同条件）；
    #   大盘基准 = −T_base×(T1v/T_base−1)（全A 以自己为分母），合计恒 0‰——
    #   与板块页指数层「沪深全A」的 contrib 同一恒等式（合计 0.000‰ 是自洽证据，不是缺数）。
    all_parts = None
    if scope == "all":
        try:
            ap = [0.0] * 5
            n_calc = 0
            for s in ser:
                if _rs_is_bj(s):
                    continue                # 与板块页「沪深全A」同口径：剔北交所（5223 只）
                x0, x1 = _rs_v(ser, s, i_base), _x1(s)
                if x0 <= 0 and x1 <= 0:
                    continue
                n_calc += 1
                a0, sh0 = st[d_base].get(s) or (None, None)
                a1, sh1 = _a1s1(s)
                q = parts_fn(x0, x1, a0, sh0, a1, sh1)
                for k in range(5):
                    ap[k] += q[k]
            all_parts = {
                "px": round(ap[0] / T1v * 1000.0, 3),
                "sh": round(ap[1] / T1v * 1000.0, 3),
                "act": round(ap[2] / T1v * 1000.0, 3),
                "new": round(ap[3] / T1v * 1000.0, 3),
                "susp": round(ap[4] / T1v * 1000.0, 3),
                "den": round(-T_base * (T1v / T_base - 1.0) / T1v * 1000.0, 3),
                # 合计实算不硬写 0：明细（stock_amv 全部行）与 daily_total 完全对齐时
                # 自然为 0；万一两边口径有缺口，这个值如实把差额摆出来，不藏。
                "total": round((sum(ap) - T_base * (T1v / T_base - 1.0)) / T1v * 1000.0, 3),
                "n_calc": n_calc,
            }
        except Exception:  # noqa: BLE001 - 分项失败不影响主表
            all_parts = None

    payload = {
        "ok": True, "asof": (snap.get("asof") if (snap and snap.get("asof")) else d1),
        "prev": d_base,
        "live_mode": bool(snap), "live_date": (snap.get("date") if snap else None),
        "live_err": (live_err or None) if want_live else None,
        "level": lvl, "level_label": label, "n": len(rows),
        "total_pct": round((T1v / T_base - 1.0) * 100.0, 3),
        "note": _rs_note(lvl),
        "boards": rows, "sel": sel,
        "sel_name": names_s.get(sel, sel),
        "sel_contrib": sel_row["contrib"] if sel_row else None,
        "n_members": n_members, "sel_parts": sel_parts, "members": members,
        "scope": scope,
    }
    # 只在 scope=all 追加：board 模式的键集必须与旧端逐字一致（不加多余键）
    if all_parts is not None:
        payload["all_parts"] = all_parts
    _rs_cache_put(key, payload)
    # 只落“有内容且数据日与库一致”的结果；空榜/取数夹生一律不写。
    if not want_live and payload.get("boards") and str(payload.get("asof") or ""):
        _rs_disk_put(ctx, key, disk_ver, payload)
    return payload


# ---------------------------------------------------------------- 组装端点
def build_status(ctx: Any) -> Dict[str, Any]:
    """引擎状态徽章（旧端 `api_status` 同字段，逐字段对齐 8800）。

    `{code,label,state,mode,kill_switch_active,kill_reason,trade_mode,updated_at}`
    - 2026-11-07：签收链整体删除（主人拍板），不再输出 `battle_plan_signed`，状态也不再由 sign 派生；
      只剩「急停 / 运行」两态。旧页（/legacy）已同步不再展示签收位。
    - 急停态：`engine/state/kill_switch.json`（旧端 `_load_kill_switch()` 读的同一份文件，
      0.3.0 起经 `ctx.state_json("kill_switch")` 可读）；该文件读不到 → 退
      `dashboard.json::engine.kill_switch`（同源快照，`_refresh_dashboard` 急停后即时重建）。
    - `trade_mode`：`engine_state.json` 的 `trade_mode`；文件缺失/读不到 → `"live"`。
    """
    dashboard = _panel(ctx, "dashboard") or {}
    engine = dashboard.get("engine") or {}
    kill = engine.get("kill_switch") or {}

    state_doc = _state(ctx, STATE_ENGINE)
    kill_doc = _state(ctx, STATE_KILL)
    if kill_doc is not None:
        kill_active = bool(kill_doc.get("active"))
        kill_reason = kill_doc.get("reason")
    else:
        kill_active = bool(kill.get("active"))
        kill_reason = kill.get("reason")
    trade_mode = (state_doc or {}).get("trade_mode", "live")
    if kill_active:
        code, label = "FROZEN", "已急停"
    else:
        code, label = "RUNNING", "运行中"
    return {
        "code": code,
        "label": label,
        "state": "FROZEN" if kill_active else "ARMED",
        "mode": "ARMED",
        "kill_switch_active": kill_active,
        "kill_reason": kill_reason,
        "trade_mode": trade_mode,
        "updated_at": _now(),
    }


def build_push_types() -> Dict[str, Any]:
    """推送能力自省（旧端 `api_push_types` 同字段）：常量端点，见模块头注释。"""
    return {"types": list(_PUSH_TYPE_KEYS),
            "labels": dict(_PUSH_TYPE_LABELS),
            "all_whitelist": sorted(_PUSH_EVENT_WHITELIST),
            "channel": dict(_PUSH_CHANNEL)}


def build_alert_plan(ctx: Any) -> Dict[str, Any]:
    """长期预警清单（旧端 `_alert_plan_doc` 同口径）：文件缺失/坏 → **空骨架 + 200**（非 404）。"""
    data = _panel(ctx, "alert_plan_persistent")
    if not isinstance(data, dict) or not isinstance(data.get("plans"), list):
        return {"meta": {"persistent": True, "generated_at": "", "source": "server"},
                "plans": []}
    return data


def build_concept(ctx: Any) -> Dict[str, Any]:
    """概念 Tab：**已移交独立 `concept` 插件**（2026-10-23，任务书 4.2）。

    历史：本批（compat 第 1 批）时该端点的数据源 `夏天-activity/概念追踪/*.md`
    不在任何只读白名单，故只能返回 503 + 原因；现在 core 新增了 `data.text` /
    `ctx.text_dir()` 目录级只读白名单，`/api/concept.json` 由 `app/plugins/concept/` 实现，
    并同样以 root 挂到 `/api/concept.json`。

    **保留本函数仅为兼容旧测试/旧调用方的导入**（不再挂在 router 上，生产不可达）：
    直接调用仍然明确报「请用 concept 插件」，不伪造结构、不猜字段。
    """
    raise HTTPException(
        status_code=503,
        detail="concept.json 已移交独立 concept 插件（app/plugins/concept/，root 挂载 /api/concept.json）",
    )


# ---------------------------------------------------------------- 路由
def register(ctx: Any) -> APIRouter:
    router = APIRouter()

    # ---- §2.1 静态 JSON 转发（原样透传，不重排、不补字段）----
    @router.get("/dashboard.json")
    def dashboard_json() -> Dict[str, Any]:
        """首屏数据。旧端 `WHITELIST` 含 dashboard → `_serve_doc` 直读同一文件，
        故**透传即逐字段一致**（见模块头注释 1）。"""
        return _panel_or_404(ctx, "dashboard")

    @router.get("/battle_plan.json")
    def battle_plan_json() -> Dict[str, Any]:
        return _panel_or_404(ctx, "battle_plan")

    @router.get("/amv_live.json")
    def amv_live_json() -> Dict[str, Any]:
        return _panel_or_404(ctx, "amv_live")

    @router.get("/amv_regime_latest.json")
    def amv_regime_json() -> Dict[str, Any]:
        return _panel_or_404(ctx, "amv_regime_latest")

    @router.get("/position_cap_latest.json")
    def position_cap_json() -> Dict[str, Any]:
        return _panel_or_404(ctx, "position_cap_latest")

    @router.get("/alert_todo.json")
    def alert_todo_json() -> Dict[str, Any]:
        return _panel_or_404(ctx, "alert_todo")

    @router.get("/alert_plan.json")
    def alert_plan_json() -> Dict[str, Any]:
        """旧端**故意不用 404**：缺失时给空 plans 骨架，让前端渲染「无长期预警」。"""
        return build_alert_plan(ctx)

    @router.get("/push_types.json")
    def push_types_json() -> Dict[str, Any]:
        return build_push_types()

    # 2026-10-23（4.2）：`/api/concept.json` 已由独立 `concept` 插件以 root 挂载实现，
    # 本插件的 503 骨架路由**已删除**（否则两个 root 插件抢同一路径，core 启动即报冲突）。
    # 见 app/plugins/concept/backend.py 与 docs/task_concept_4_2.md。

    # ---- shell 层（2026-10-23 补：第 1 批任务书漏列，导致旧外壳显示“引擎服务未启动”浮层）----
    # 旧外壳 `shell.html` 调 `/api/shell/status`，拿不到时就认为引擎挂了（code=DOWN），
    # 弹层遮住整个内容区。其实 `/api/shell/status` 只是把 `/api/status` 包一层：
    #   {panel_up: true, engine: <status>, ts: <now>}
    # 实测旧 8801 `engine/server.py::shell_server` 同构。
    @router.get("/shell/status")
    def shell_status() -> Dict[str, Any]:
        st = build_status(ctx)
        ts = st.get("updated_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # 2026-11-07：带上交易日时钟（页面顶部亮「数据日」）。门面不可用 → 空对象，前端不显示。
        try:
            clock = ctx.trade_clock.status()
        except Exception:  # noqa: BLE001 - 旧 core / 日历异常不拖垮状态灯
            clock = {}
        return {"panel_up": True, "engine": st, "ts": ts, "data_clock": clock}

    @router.get("/shell/version")
    def shell_version() -> Dict[str, Any]:
        """外壳用 `v` 判断内容页是否需要重载（变了就刷新 iframe）。

        旧 8801 返回一个恒定的构建时间戳。这里用**进程启动时刻**（模块导入时算一次），
        既不会每请求都变（否则前端会不断重载），也能在重启后让客户端刷新。
        """
        return {"v": _SHELL_VERSION}

    # ---- 外壳缺失端点（批次 0，2026-10-24）：旧壳在 8810 下原样可用 ----
    @router.post("/shell/search")
    def shell_search(body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """侧栏搜索（旧 8801 `/api/shell/search` 的同源版）：转发语义=旧端，目标改本进程。

        旧端由外壳宿主 8801 代理 8800 的 `/api/search/all`；v2 外壳与内容同源（8810），
        故直接调 `build_search`，不做 HTTP 自环。请求体 {q, limit}。
        """
        payload = body if isinstance(body, dict) else {}
        return build_search(ctx, q=payload.get("q"), limit=payload.get("limit"))

    # ---- §2.2 需要组装的只读端点 ----
    @router.get("/positions.json")
    def positions_json() -> Dict[str, Any]:
        """旧端同样走 `/api/{name}.json` 白名单直读 → 透传即一致
        （`{meta,date,updated_at,holdings,selection,track_signals,source_audit}` 全在里面）。"""
        return _panel_or_404(ctx, "positions")

    @router.get("/gates")
    def gates() -> Dict[str, Any]:
        return build_gates(ctx)

    @router.get("/status")
    def status() -> Dict[str, Any]:
        return build_status(ctx)

    @router.get("/real_positions")
    def real_positions() -> Dict[str, Any]:
        return build_real_positions(ctx)

    @router.get("/alerts")
    def alerts() -> Dict[str, Any]:
        """告警三源合一。注意：**不是** alert_todo.json 的 alerts 段（任务书 §2.2 的猜测
        与旧端实现不符）；旧端是真·内存态评估端点，这里按旧端结构复刻。"""
        return build_alerts(ctx)

    @router.get("/search/all")
    def search_all(q: str = Query(default=""), limit: Optional[int] = Query(default=None)) -> Dict[str, Any]:
        return build_search(ctx, q=q, limit=limit)

    @router.post("/search/all")
    def search_all_post(body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """旧前端是 `fetch("/api/search/all", {method:"POST", body: JSON})`。
        兼容层两个方法都接（旧前端一行不改即可用）。"""
        payload = body if isinstance(body, dict) else {}
        return build_search(ctx, q=payload.get("q"), limit=payload.get("limit"))

    # ---- amv_research（0.2.0）：双路径，旧端两个装饰器叠同一 handler ----
    # 旧端：`@app.get("/api/amv_research.json")` + `@app.get("/api/amv_research")`，
    # 响应键逐字一致（旧前端只用 `.json` 那条，裸路径是给人/工单手测用）。
    @router.get("/amv_research.json")
    def amv_research_json(level: str = Query(default="L2"), code: str = Query(default=""),
                          days: int = Query(default=30), live: int = Query(default=0),
                          scope: str = Query(default="board")) -> Dict[str, Any]:
        return build_amv_research(ctx, level=level, code=code, days=days,
                                  live=live, scope=scope)

    @router.get("/amv_research")
    def amv_research(level: str = Query(default="L2"), code: str = Query(default=""),
                     days: int = Query(default=30), live: int = Query(default=0),
                     scope: str = Query(default="board")) -> Dict[str, Any]:
        return build_amv_research(ctx, level=level, code=code, days=days,
                                  live=live, scope=scope)

    # ---- 通配 `/api/{name}.json`（0.2.0）：**必须最后注册**，且自带让路逻辑（见函数注释）----
    register_wildcard(router, ctx)

    return router


def _collect_literal_paths(routes: Any, prefix: str, out: set) -> None:
    """递归收集一组路由里的**字面路径**（不含 `{}` 参数），带上 include 前缀。

    兼容 FastAPI 两种布局：旧版 `include_router` 把子路由拍平进 `app.routes`；
    ersion ≥0.140 用 `_IncludedRouter` 嵌套包装（`original_router` + `include_context.prefix`）。
    两种都走得到。拿不准时宁可少收集（退化为「通配自接」，不会误让路）。
    """
    for route in routes or ():
        child = getattr(route, "original_router", None)
        if child is not None:
            ctx = getattr(route, "include_context", None)
            sub = getattr(ctx, "prefix", "") or ""
            _collect_literal_paths(getattr(child, "routes", None), prefix + sub, out)
            continue
        path = getattr(route, "path", None)
        if isinstance(path, str) and path and "{" not in path:
            out.add(prefix + path)


def shadow_aware_route_class() -> type:
    """为通配路由生成一个「让路」的 APIRoute 子类。

    **为什么需要这层**（真实风险，已实测）：compat 按目录名排序**先于** compat_sector /
    compat_watchlist / concept / ops / review 加载，而它们都是 `mount="root"` 的
    `/api/<名>.json` 具体路由。FastAPI 按注册顺序匹配 ⇒ 若 compat 的 `/{name}.json`
    先注册，就会把 `/api/sector_card.json` 抢走（实测：返回通配体自己的 404）。

    所以不靠「注册顺序」硬碰，改为**运行期让路**：通配先匹配，若发现同一应用里存在一条
    **字面路径完全相同**的具体路由，就返回 `Match.NONE` 把自己让出去，让 starlette
    继续往后找。效果等价于「具体路由在前」，且以后新加插件不需要回来改 compat。

    代价：每个请求一次字面路径集合查表（集合按 app 缓存一次，不重建）。
    """

    class _WildcardRoute(APIRoute):
        # id(app) → (app, 字面路径集合)。存 app 引用是为了防 **id 复用**：
        # 旧 app 被回收后新 app 可能拿到同一个 id，只看 id 会读到别人的路径集。
        _literal_cache: Dict[int, Tuple[Any, frozenset]] = {}

        def _app_literals(self, app: Any) -> frozenset:
            key = id(app)
            hit = self._literal_cache.get(key)
            if hit is not None and hit[0] is app:
                return hit[1]
            out: set = set()
            with contextlib.suppress(Exception):
                _collect_literal_paths(getattr(getattr(app, "router", None), "routes", None),
                                       "", out)
            frozen = frozenset(out)
            self._literal_cache[key] = (app, frozen)
            return frozen

        def matches(self, scope):
            match, child = super().matches(scope)
            if match is Match.NONE:
                return match, child
            path = scope.get("path", "")
            app = scope.get("app")
            if app is None:
                return match, child
            # 自己这条路径含 `{name}`，不会被收进字面集合，所以不用排除自己
            if path in self._app_literals(app):
                return Match.NONE, {}
            return match, child

    return _WildcardRoute


def register_wildcard(router: APIRouter, ctx: Any) -> None:
    """注册 `/api/{name}.json` 通配（旧端 `engine/server.py::api_doc` 的只读等价）。

    旧端只在 `WHITELIST` 里直读 `data/panel/<name>.json`；新版把白名单统一到 core 的
    `paths.READABLE_PANEL`（唯一源，本插件不自建第二份名单）。
    安全边界：名字必须以白名单键命中，插件**不做任何路径拼接** ⇒ 不可能任意文件读
    （`../../x` / `C:/x` 这类名字根本不在白名单，直接 404）。
    """

    def panel_json_wildcard(name: str) -> Any:
        if name not in _PANEL_JSON_NAMES:
            raise HTTPException(status_code=404, detail="未知文档 %s" % name)
        getter = getattr(ctx, "panel_json", None)
        if not callable(getter):
            raise HTTPException(status_code=500, detail="ctx.panel_json 不可用")
        try:
            return getter(name)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="无数据：%s（%s）" % (name, exc))
        except PermissionError as exc:
            # 名字在 core 白名单、但没在本插件 manifest 声明 → 如实 404，不装 200
            raise HTTPException(status_code=404,
                                detail="无数据：%s（未在 compat 清单声明：%s）" % (name, exc))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail="%s 读取失败：%s" % (name, exc))

    router.add_api_route("/{name}.json", panel_json_wildcard, methods=["GET"],
                         include_in_schema=False,
                         route_class_override=shadow_aware_route_class())