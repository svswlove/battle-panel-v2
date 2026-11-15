# -*- coding: utf-8 -*-
"""paper 插件后端：旧面板前端的**纸面交易** HTTP 薄壳（`backend.mount = "root"`）。

## 这个插件是什么

旧前端（`engine/web/index.html` 与 `kline_order.js`）直接请求根路径 `/api/order/place`、
`/api/paper_account`、`/api/ledger` 等；本插件用 `backend.mount = "root"` 注册到 `/api`，
把它们全部接到新的 core 纸面服务（`app/core/paper/`）上。

**业务逻辑一行都不在这里**：下单校验、撮合、记账、条件单状态机、文件锁、原子写、
备份与审计全部在 `app/core/paper/{service,orders,broker,state}.py`。
本文件只做三件事：

1. HTTP 参数校验（Pydantic body / Query 类型）；
2. 调 `ctx.paper`（`PaperOrderService` 单例）与 `ctx.feed`（行情单例）；
3. 把结果装配成旧前端认得的 JSON（装配细节在 `views.py`，纯函数）。

## 启动装配（任务书 §2.4）

`register(ctx)` 里把 core 的行情订阅与纸面服务接起来：

- `ctx.feed.set_interest_provider(lambda: svc.wanted_codes())`：订阅清单（持仓 + ACTIVE
  条件单 + 未成交挂单）由 supervisor 每 15 秒重读，新增标的自动增量订阅；
- `ctx.feed.ensure(svc.wanted_codes())`：启动即补一次，不等 15 秒；
- `ctx.feed.add_listener(lambda q: svc.on_quotes(q))`：tick 驱动的条件单评估 + 挂单补成交；
- `svc._session_ok = _make_session_ok(ctx)`：真实交易日历（eltdx WorkdayService），
  日历不可用时退化为「工作日 + 连续竞价时段」，**日历异常 fail-open=True**。

## 已知缺口（不在本任务范围）

`signal_buy`（信号买入）条件单需要信号库（`signals/library.py`，下一批接入），
本任务**不接**：`svc.signal_provider` 保持 `None`，前端可以建单、但永不触发。

## 硬约束

- 不写任何文件（账本写入全在 `app/core/paper/`），无 `open()`、无 sqlite、无 HTTP、无子进程；
- 不 import 任何行情/交易库；行情一律走 `ctx.feed` / `ctx.quotes`；
- JSON 一律 UTF-8 + `ensure_ascii=False`（FastAPI 的 JSONResponse 默认即此）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

# 装配层（纯函数：账本/报价 → 对外 JSON）。
#
# 三级导入，都是为了「插件按文件路径加载」这个现实（`contract.load_plugin` 用
# `spec_from_file_location("_panel_v2_plugin_paper_backend", ...)`）：那种情况下本模块
# 没有父包，相对导入不存在；项目根在 sys.path 时走绝对导入；都不行就按文件路径加载。
try:                                        # 1) 正常包导入（测试/其他模块 import）
    from . import views  # type: ignore
except ImportError:                         # pragma: no cover - 取决于加载方式
    try:                                    # 2) core 的插件加载方式（无父包）
        from app.plugins.paper import views  # type: ignore
    except ImportError:                     # 3) 最后兜底：按文件路径加载同一个文件
        import importlib.util as _ilu
        from pathlib import Path as _Path

        _spec = _ilu.spec_from_file_location(
            "_panel_v2_plugin_paper_views", _Path(__file__).with_name("views.py"))
        if _spec is None or _spec.loader is None:      # pragma: no cover - 环境异常
            raise
        views = _ilu.module_from_spec(_spec)  # type: ignore[assignment]
        _spec.loader.exec_module(views)      # type: ignore[union-attr]

# 契约里定义的业务拒绝异常（core 已 vendored，插件只负责转 HTTP 状态码）
try:                                    # 正常路径
    from app.core.paper.orders import PaperOrderError  # type: ignore
except Exception:                       # noqa: BLE001 - 极端情况下退化成本地定义（保持插件可加载）
    class PaperOrderError(Exception):   # type: ignore[no-redef]
        """业务拒绝（兜底定义；正常路径用 core 的那一个）。"""

        def __init__(self, message: str, code: str = "invalid", status: int = 422):
            super().__init__(message)
            self.code = code
            self.status = status


# ---------------------------------------------------------------- 小工具


def _now_str() -> str:
    """统一时间口径（YYYY-MM-DD HH:MM:SS）。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _today8() -> str:
    """当日 8 位日期（委托/条件单口径统一 YYYYMMDD）。"""
    return datetime.now().strftime("%Y%m%d")


def _http_error(exc: PaperOrderError) -> HTTPException:
    """`PaperOrderError` → HTTPException（status 由 core 给，detail 带机器码便于前端分支）。"""
    status = int(getattr(exc, "status", 422) or 422)
    code = getattr(exc, "code", "") or ""
    return HTTPException(status_code=status, detail=f"{exc}（{code}）" if code else str(exc))


def _to_ts_code(ctx: Any, raw: Any) -> Optional[str]:
    """代码归一 → `600519.SH`；无法判定 → None（不猜）。

    走 `ctx.quotes.to_ts_code`（core 行情门面，与下单行情同一份口径表），
    避免插件里再抄一份交易所后缀规则。
    """
    try:
        return ctx.quotes.to_ts_code(str(raw or ""))
    except Exception:                   # noqa: BLE001 - 门面不可用 → 按非法代码处理
        return None


def _names(ctx: Any) -> Dict[str, str]:
    """代码 → 名称表（`ctx.json_read("stock_names")` 的 `names` 段）；取不到 → 空表。

    空表时下游一律回退显示代码（**不编造名称**）。
    """
    try:
        doc = ctx.json_read("stock_names")
    except Exception:                   # noqa: BLE001 - 未声明/文件缺失 → 空表
        return {}
    if isinstance(doc, dict):
        table = doc.get("names")
        if isinstance(table, dict):
            return {str(k): str(v) for k, v in table.items()}
    return {}


def _frozen_by_code(svc: Any, codes: List[str]) -> Dict[str, int]:
    """按持仓码取挂单冻结股数（逐票调 `svc.frozen_qty`，core 侧唯一口径）。"""
    out: Dict[str, int] = {}
    for code in codes:
        try:
            out[code] = int(svc.frozen_qty(code) or 0)
        except Exception:               # noqa: BLE001 - 单票失败按 0 冻结（不阻断账本）
            out[code] = 0
    return out


def _order_metrics(ctx: Any, codes: List[str]) -> Dict[str, Dict[str, Any]]:
    """批量取 ATR14 / MA5（保本止损、破五日线条件单用）；单只失败给 null 对。

    走 `ctx.quotes.order_metrics()`（前复权日线，与下单展示价同口径）。
    取不到就留 `{"atr14": None, "ma5": None}` —— **不补零、不编造**。
    """
    out: Dict[str, Dict[str, Any]] = {}
    for code in codes:
        empty = {"atr14": None, "ma5": None}
        try:
            glb = ctx.quotes.normalize(code)
            if not glb:
                out[code] = empty
                continue
            got = ctx.quotes.order_metrics(glb) or {}
            out[code] = {"atr14": got.get("atr14"), "ma5": got.get("ma5")}
        except Exception:               # noqa: BLE001 - 行情源不可用 → 空指标
            out[code] = empty
    return out


def _quote_brief(quotes: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """条件单评估回执里的行情摘要：`{code: {last, quote_time, fresh, src}}`。"""
    out: Dict[str, Dict[str, Any]] = {}
    for code, q in (quotes or {}).items():
        if not isinstance(q, dict):
            continue
        out[str(code)] = {"last": q.get("last"), "quote_time": q.get("quote_time"),
                          "fresh": q.get("fresh"), "src": q.get("src")}
    return out


# ---------------------------------------------------------------- 交易时段


def _make_session_ok(ctx: Any):
    """造一个「是否在可交易时段」的判定函数（注入给 `svc._session_ok`）。

    口径（任务书 §2.4）：
    - 时段固定连续竞价：09:30–11:30 / 13:00–15:00；
    - 工作日优先问 eltdx 的 WorkdayService（`ctx.quotes.call("workdays.is_workday", d)`），
      不可用（返回 None / 非布尔）→ 退化为 `weekday() < 5`；
    - **日历调用抛异常 → fail-open 返回 True**：纸面演练不因日历故障整日停摆
      （与旧 `ManualOrderService._default_session_ok` 同口径，宁可放行时段内下单）。

    注意顺序：先判时段再看日历。时段外（夜里/午休）一律 False —— 夜里下市价单没有对手盘，
    fail-open 不能把这条安全阀也放掉。
    """
    facade_holder: Dict[str, Any] = {}

    def _session_ok() -> bool:
        now = datetime.now()
        hm = now.hour * 60 + now.minute
        if not ((570 <= hm < 690) or (780 <= hm < 900)):
            return False                      # 非连续竞价时段
        try:
            facade = facade_holder.get("q")
            if facade is None:
                facade = ctx.quotes         # 只读行情门面（日历查询走同一条连接）
                facade_holder["q"] = facade
            res = facade.call("workdays.is_workday", now.date())
        except Exception:                     # noqa: BLE001 - 日历故障：fail-open（旧口径）
            return True
        if res is None:                       # 日历不可用（未连上/无此接口）
            return now.weekday() < 5
        if isinstance(res, bool):
            return res
        try:
            return bool(res)
        except Exception:                     # noqa: BLE001 - 怪异返回值一律按不可用处理
            return now.weekday() < 5

    return _session_ok


def _wire(ctx: Any) -> None:
    """`register(ctx)` 里的启动装配（任务书 §2.4）；任一环失败只记日志，**不让插件加载失败**。

    理由：装配失败最多是「条件单不自动触发 / 持仓不自动订阅」，纸面下单仍可手工操作；
    而 `register` 抛异常会让整个插件被 core 判为 broken（前端所有纸面端点 404）。

    两个闭包都写成 `ctx.paper`（**每次现取单例**）而不是闭包捕获 `svc`：
    进程单例可能被测试 `reset_service()` 丢掉，捕获旧实例会让订阅清单永远读旧账本。
    """
    svc = ctx.paper
    try:
        codes = svc.wanted_codes()
    except Exception:                     # noqa: BLE001 - 账本读失败 → 先不订阅
        codes = []
    try:
        ctx.feed.set_interest_provider(lambda: ctx.paper.wanted_codes())
        ctx.feed.ensure(codes)
        ctx.feed.add_listener(lambda batch: ctx.paper.on_quotes(batch))
    except Exception:                     # noqa: BLE001 - 行情层不可用不影响 HTTP 层
        ctx.logger.warning("paper 插件：feed 装配失败（条件单不会自动触发）", exc_info=True)
    try:
        svc._session_ok = _make_session_ok(ctx)
    except Exception:                     # noqa: BLE001 - 保留 core 缺省时段判定
        ctx.logger.warning("paper 插件：交易日历装配失败（退化为工作日+时段）", exc_info=True)
    # signal_buy 的信号库下一批接入（本任务不接）：保持 None → 该类条件单永不触发。
    if getattr(svc, "signal_provider", None) is None:
        svc.signal_provider = None


# ---------------------------------------------------------------- 请求体


class OrderPlaceBody(BaseModel):
    """`POST /api/order/place`（字段与旧端 Pydantic body 一致）。"""

    code: str
    side: str = "buy"
    qty: int
    price: Optional[float] = None
    order_type: str = "limit"
    name: str = ""
    note: str = ""


class OrderCancelBody(BaseModel):
    """`POST /api/order/cancel`。"""

    order_id: str


class ConditionAddBody(BaseModel):
    """`POST /api/condition/add`（字段与旧端 body 一致）。"""

    code: str
    type: str
    qty: int
    trigger_price: Optional[float] = None
    pct: Optional[float] = None
    amount: Optional[float] = None
    order_type: str = "market"
    limit_price: Optional[float] = None
    side: Optional[str] = None
    at: Optional[str] = None
    valid_until: Optional[str] = None
    group: Optional[str] = None
    confirm_count: int = 1
    buffer_atr: Optional[float] = None
    signal: Optional[str] = None
    # 2026-10-28：回踩均线 / 关键K 的目标位与容差（auto/yellow/white 或 auto/upper/lower）
    target: Optional[str] = None
    tol: Optional[float] = None
    name: str = ""
    note: str = ""


class ConditionCancelBody(BaseModel):
    """`POST /api/condition/cancel`。"""

    cond_id: str


# ---------------------------------------------------------------- 路由


def register(ctx: Any) -> APIRouter:
    """插件入口：返回 APIRouter（manifest `mount=root` ⇒ core 挂到 `/api`）。"""
    _wire(ctx)
    router = APIRouter()

    # ---- 下单 / 撤单 -----------------------------------------------------

    @router.post("/order/place")
    def order_place(body: OrderPlaceBody) -> Dict[str, Any]:
        """下手工单（纸面）：参数校验 → `svc.place` → 回执。

        行情从 `svc.quote_for` 取（feed 的 eltdx 打底 + 5 秒内盘口覆盖）。
        业务拒绝（无行情 / 资金不足 / 非交易时段…）由 core 抛 `PaperOrderError`，
        这里转成对应 HTTP 状态码，`detail` 里带 `（code）` 供前端分支。
        """
        svc = ctx.paper
        code = _to_ts_code(ctx, body.code)
        quote = svc.quote_for(code) if code else {}
        try:
            res = svc.place(code or body.code, body.side, body.qty, price=body.price,
                            order_type=body.order_type, quote=quote,
                            source="manual", name=body.name, note=body.note)
        except PaperOrderError as exc:
            raise _http_error(exc)
        return {"ok": True, "order": res.get("order") or {},
                "warnings": res.get("warnings") or [],
                "account": res.get("account") or {},
                "mode": svc.trade_mode()}

    @router.post("/order/cancel")
    def order_cancel(body: OrderCancelBody) -> Dict[str, Any]:
        """撤单（纸面）。已成交/已撤/查无此单一律 4xx（不静默成功）。"""
        svc = ctx.paper
        try:
            res = svc.cancel_order(body.order_id)
        except PaperOrderError as exc:
            raise _http_error(exc)
        return {"ok": True, "order_id": res.get("order_id"), "status": res.get("status")}

    @router.get("/order/list")
    def order_list(date: Optional[str] = Query(default=None)) -> Dict[str, Any]:
        """当日委托 + 条件单 + 账户摘要 + 指标（旧端 `api_order_list` 同结构）。"""
        svc = ctx.paper
        conds = svc.list_conditions()
        acc = svc.account_view()
        codes = {c["code"] for c in conds if isinstance(c, dict)
                 and c.get("status") == "ACTIVE" and c.get("code")}
        codes |= set((acc.get("positions") or {}).keys())
        metrics = _order_metrics(ctx, sorted(codes))
        return {"ok": True, "mode": svc.trade_mode(), "date": date or _today8(),
                "orders": svc.list_orders(date), "conditions": conds,
                "account": acc, "metrics": metrics}

    # ---- 条件单 ---------------------------------------------------------

    @router.post("/condition/add")
    def condition_add(body: ConditionAddBody) -> Dict[str, Any]:
        """加条件单（9 类 + OCO/有效期/急停全部由 core 口径把关）。

        带 `quote` 传入是为了让 core 做「触发价与现价的相对位置」校验
        （止损价必须低于现价、止盈价必须高于现价），否则一提交就立刻触发，
        用户以为挂了条件单、其实已经变成市价单报出去了。
        """
        svc = ctx.paper
        code = _to_ts_code(ctx, body.code) or body.code
        try:
            rec = svc.add_condition(
                code, body.type, body.qty,
                trigger_price=body.trigger_price, pct=body.pct, amount=body.amount,
                order_type=body.order_type, limit_price=body.limit_price,
                side=body.side, at=body.at, valid_until=body.valid_until,
                group=body.group, confirm_count=body.confirm_count,
                buffer_atr=body.buffer_atr, signal=body.signal,
        target=body.target, tol=body.tol,
                quote=svc.quote_for(code), name=body.name, note=body.note)
        except PaperOrderError as exc:
            raise _http_error(exc)
        return {"ok": True, "condition": rec}

    @router.post("/condition/cancel")
    def condition_cancel(body: ConditionCancelBody) -> Dict[str, Any]:
        """撤条件单：已终态 → 409（不静默成功）。"""
        svc = ctx.paper
        try:
            rec = svc.cancel_condition(body.cond_id)
        except PaperOrderError as exc:
            raise _http_error(exc)
        return {"ok": True, "condition": rec}

    @router.get("/condition/list")
    def condition_list(code: Optional[str] = Query(default=None),
                       status: Optional[str] = Query(default=None)) -> Dict[str, Any]:
        """条件单列表（可按代码/状态过滤）。"""
        svc = ctx.paper
        return {"ok": True, "conditions": svc.list_conditions(code=code, status=status)}

    @router.post("/condition/evaluate")
    def condition_evaluate(force: int = Query(default=0)) -> Dict[str, Any]:
        """手动评估一轮（面板「立即评估」）：`evaluate` + `match_pending`。

        `force=1` 绕过交易时段与行情新鲜度门槛（自测/演练用，成交仍走 place 的完整校验）。
        """
        svc = ctx.paper
        conds = svc.list_conditions(status="ACTIVE")
        codes = {c["code"] for c in conds if isinstance(c, dict) and c.get("code")}
        for o in svc.list_orders():
            if isinstance(o, dict) and str(o.get("status")) in ("REPORTED", "PARTIAL"):
                code = o.get("ts_code") or o.get("code")
                if code:
                    codes.add(str(code))
        quotes = svc.quote_map(sorted(codes))
        res = svc.evaluate(quotes, force=bool(force)) or {}
        filled = svc.match_pending(quotes) or []
        return {"ok": True,
                "triggered": res.get("triggered") or [],
                "cancelled": res.get("cancelled") or [],
                "expired": res.get("expired") or [],
                "filled": filled,
                "quotes": _quote_brief(quotes)}

    # ---- 账户 / 账本 ----------------------------------------------------

    @router.get("/paper_account")
    def paper_account() -> Dict[str, Any]:
        """纸面独立账本 + 持仓估值（旧端 `api_paper_account` 同构）。

        账本文件尚未生成（一次成交都没有）→ 404，与旧端一致（前端显示「无成交记录」）。
        「有没有账本文件」用 `service_status()["files"]["account"]` 判断，
        避免插件自己去做文件 IO。
        """
        from app.core.paper.service import service_status  # noqa: PLC0415 - 装配层查询

        svc = ctx.paper
        if not service_status(svc)["files"]["account"]:
            raise HTTPException(status_code=404,
                                detail="纸面账本未生成（尚无纸面成交）")
        acct = svc.account() or {}
        positions = acct.get("positions") or {}
        names = _names(ctx)
        quotes = views.quote_records(svc.quote_map(list(positions.keys())))
        # 可卖量以 core 口径为准（T+1 近似 + 挂单冻结），账本字段只作兜底
        rows_in = []
        for code, p in positions.items():
            row = dict(p) if isinstance(p, dict) else {}
            try:
                row["available_qty"] = int(svc.available_qty(code))
            except Exception:           # noqa: BLE001 - 单票失败用账本字段兜底
                pass
            rows_in.append((code, row))
        return views.build_paper_account(acct, rows_in, quotes, names, now=_now_str())

    @router.get("/ledger")
    def ledger(mode: str = Query(default="paper")) -> Dict[str, Any]:
        """账本（仪表盘唯一数据源）：账户摘要 + 持仓 + 当日成交 + 附属两本账。

        `mode=paper` → 真实纸面账本；`mode=live` → **明确拒绝**（HTTP 200 + ok=false），
        实盘账本仍在旧 8800 看：新版纸面模块没有实盘读通道，也不假装有（前端不会崩）。
        """
        if mode not in ("paper", "live"):
            raise HTTPException(status_code=400,
                                detail="非法 mode：%r（paper/live）" % (mode,))
        if mode == "live":
            return {"ok": False, "mode": "live",
                    "error": "paper-only: 实盘账本请在旧 8800 查看"}
        svc = ctx.paper
        acct = svc.account() or {}
        try:
            trades_all = svc.broker.load_trades() or []
        except Exception:               # noqa: BLE001 - 流水读失败 → 空表（账本主数据照出）
            trades_all = []
        conditions = svc.list_conditions()
        orders = svc.list_orders()
        positions = acct.get("positions") or {}
        names = _names(ctx)
        quotes = views.quote_records(svc.quote_map(list(positions.keys())))
        return views.build_ledger(acct, trades_all, conditions, orders, quotes, names,
                                  frozen_cash=svc.frozen_cash(),
                                  frozen_by_code=_frozen_by_code(svc, list(positions.keys())),
                                  now=_now_str())

    # ---- K 线买卖点 -----------------------------------------------------

    @router.get("/trades/{ts_code}")
    def trades(ts_code: str, days: int = Query(default=60)) -> Dict[str, Any]:
        """买卖点（K 线图上的 B/S 点）：只含**已完成**的纸面成交（`status=FILLED`）。

        `cost.paper` = 纸面持仓成本；`cost.live` = `real_positions` 里该码的成本
        （≤0 视为无效 → null，前端不画 y=0 的假成本线）。非法代码/参数 → 400。
        """
        code = _to_ts_code(ctx, ts_code)
        if not code:
            raise HTTPException(status_code=400,
                                detail="非法 ts_code：%r（需 6 位代码.SH/SZ/BJ）" % (ts_code,))
        try:
            days_i = int(days)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400,
                                detail="非法 days：%r（需整数）" % (days,))
        svc = ctx.paper
        try:
            paper_trades = svc.broker.load_trades() or []
        except Exception:               # noqa: BLE001 - 流水不可读 → 无点（不编造）
            paper_trades = []
        cost_paper = views.valid_cost(((svc.account() or {}).get("positions") or {})
                                      .get(code, {}).get("cost_price"))
        return views.build_trades_payload(
            code, days_i, paper_trades, _today8(),
            cost_paper=cost_paper, cost_live=_cost_live(ctx, code))

    # ---- 诊断 -----------------------------------------------------------

    @router.get("/paper/feed/status")
    def feed_status() -> Dict[str, Any]:
        """行情订阅健康（新增诊断端点，排查用）：直接回 feed 的 `status()`。"""
        return ctx.feed.status()

    return router


def _cost_live(ctx: Any, code: str) -> Optional[float]:
    """实盘成本线：`real_positions.json` 里该码的 `cost_price`（须 >0，旧端同口径）。

    读不到（未声明/文件缺失/无该持仓）→ None，**不猜也不拿纸面成本冒充**。
    """
    try:
        doc = ctx.panel_json("real_positions")
    except Exception:                   # noqa: BLE001 - 未声明/文件缺失 → 无成本线
        return None
    for row in ((doc or {}).get("positions") or []):
        if not isinstance(row, dict):
            continue
        if str(row.get("code") or "").upper() != str(code or "").upper():
            continue
        return views.valid_cost(row.get("cost_price"))
    return None
